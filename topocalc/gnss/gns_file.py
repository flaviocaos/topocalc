"""
gns_file.py
===========
Leitura do cabeçalho dos arquivos brutos .GNS da Hi-Target (D8 Pro, V90...).

Layout obtido por engenharia reversa e validado com 12 arquivos reais de
receptores D8 Pro (levantamento HCB, Angola, out/2026):

    0x000  "ZHD COLLECTED DATA FILE2"              assinatura
    0x019  "ver 100.0"                             versão do formato
    0x031  "Date:AAAA/M/D"                         data do rastreio
    0x05A  float64 LE  latitude  (rad)             posição de navegação
    0x062  float64 LE  longitude (rad)             (autônoma, erro de metros)
    0x06A  float32 LE  altitude elipsoidal (m)
    0x06E  uint32 LE   intervalo de gravação (ms)
    0x082  uint16 LE   altura da antena (mm)
    0x08C  "HITV200"                               código do modelo
    0x0A0  "[D8 Pro]"                              nome do modelo
    0x0B3  "ID:<serial>-M-D,hh:mm"                 número de série + hora local
    0x2A0  início das observações brutas

O bloco de observações vem em um de dois formatos, conforme a placa GNSS
interna do receptor:
    * NovAtel OEM binário (sync AA 44 12 1C; RANGECMP + efemérides)
    * Trimble (pacotes STX/ETX 02..03; RAWDATA 57h registro 6 = RT27)
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

SIGNATURE = b"ZHD COLLECTED DATA FILE"
DATA_OFFSET = 0x2A0

FORMAT_NOVATEL = "novatel"
FORMAT_TRIMBLE = "trimble_rt27"
FORMAT_UNKNOWN = "desconhecido"


@dataclass
class GnsHeader:
    path: Path
    size: int
    signature_ok: bool
    format_version: Optional[str] = None
    survey_date: Optional[date] = None
    approx_lat_deg: Optional[float] = None
    approx_lon_deg: Optional[float] = None
    approx_h_m: Optional[float] = None
    interval_s: Optional[float] = None
    antenna_height_m: Optional[float] = None
    model_code: Optional[str] = None
    model_name: Optional[str] = None
    serial: Optional[str] = None
    data_format: str = FORMAT_UNKNOWN
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "arquivo": self.path.name,
            "tamanho_bytes": self.size,
            "assinatura_ok": self.signature_ok,
            "versao_formato": self.format_version,
            "data": self.survey_date.isoformat() if self.survey_date else None,
            "lat_aprox": self.approx_lat_deg,
            "lon_aprox": self.approx_lon_deg,
            "h_aprox": self.approx_h_m,
            "intervalo_s": self.interval_s,
            "altura_antena_m": self.antenna_height_m,
            "modelo": self.model_name or self.model_code,
            "serie": self.serial,
            "formato_dados": self.data_format,
            "avisos": list(self.warnings),
        }


def _cstr(raw: bytes, start: int, maxlen: int = 48) -> str:
    chunk = raw[start:start + maxlen]
    end = chunk.find(b"\x00")
    if end >= 0:
        chunk = chunk[:end]
    return chunk.decode("ascii", errors="ignore").strip()


def detect_data_format(raw: bytes) -> str:
    """Identifica a placa GNSS pelo primeiro pacote após o cabeçalho."""
    head = raw[DATA_OFFSET:DATA_OFFSET + 4096]
    if head[:4] == b"\xaa\x44\x12\x1c":
        return FORMAT_NOVATEL
    if len(head) > 6 and head[0] == 0x02 and head[2] in (0x55, 0x57, 0x40, 0x4B):
        return FORMAT_TRIMBLE
    # varredura de reserva: conta assinaturas nos primeiros 256 kB
    body = raw[DATA_OFFSET:DATA_OFFSET + 262144]
    n_nov = body.count(b"\xaa\x44\x12\x1c")
    n_trm = len(re.findall(rb"\x02[\x00-\xff]\x57[\x00-\xff]\x06", body))
    if n_nov > 10 and n_nov >= n_trm:
        return FORMAT_NOVATEL
    if n_trm > 10:
        return FORMAT_TRIMBLE
    return FORMAT_UNKNOWN


def read_header(path: Path) -> GnsHeader:
    raw = path.read_bytes()
    h = GnsHeader(path=path, size=len(raw), signature_ok=raw.startswith(SIGNATURE))
    if not h.signature_ok:
        h.warnings.append("Assinatura 'ZHD COLLECTED DATA FILE' não encontrada: pode não ser um .GNS Hi-Target.")
    if len(raw) < DATA_OFFSET + 64:
        h.warnings.append("Arquivo pequeno demais para conter observações.")
        return h

    m = re.search(rb"ver\s*([0-9]+(?:\.[0-9]+)?)", raw[:0x40])
    if m:
        h.format_version = m.group(1).decode()

    m = re.search(rb"Date:(\d{4})/(\d{1,2})/(\d{1,2})", raw[:0x60])
    if m:
        try:
            h.survey_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            h.warnings.append("Data do cabeçalho inválida.")

    lat, lon = struct.unpack_from("<dd", raw, 0x5A)
    (alt,) = struct.unpack_from("<f", raw, 0x6A)
    if abs(lat) <= math.pi / 2 and abs(lon) <= math.pi and (lat or lon):
        h.approx_lat_deg = math.degrees(lat)
        h.approx_lon_deg = math.degrees(lon)
        h.approx_h_m = float(alt)

    (interval_ms,) = struct.unpack_from("<I", raw, 0x6E)
    if 0 < interval_ms <= 3_600_000:
        h.interval_s = interval_ms / 1000.0

    (ant_mm,) = struct.unpack_from("<H", raw, 0x82)
    if 0 < ant_mm < 10000:
        h.antenna_height_m = ant_mm / 1000.0
    else:
        h.warnings.append("Altura da antena não encontrada no cabeçalho: informe manualmente.")

    h.model_code = _cstr(raw, 0x8C, 16) or None
    name = _cstr(raw, 0xA0, 16).strip("[]") or None
    h.model_name = name or _cstr(raw, 0xE0, 16) or None

    m = re.search(rb"ID:(\d{6,12})", raw[0xA0:0x100])
    if m:
        h.serial = m.group(1).decode()

    h.data_format = detect_data_format(raw)
    if h.data_format == FORMAT_UNKNOWN:
        h.warnings.append("Formato das observações não reconhecido (nem NovAtel nem Trimble).")
    return h


_SESSION_SUFFIX = re.compile(r"^(?P<name>.+?)(?P<doy>\d{3})(?P<sess>\d)$")


def guess_point_name(path: Path, survey_date: Optional[date]) -> str:
    """Sugere o nome do marco a partir do nome do arquivo.

    Os controladores Hi-Target costumam gravar NOME + dia do ano (3 dígitos)
    + sessão (1 dígito), ex.: HCB12790 -> HCB1 (dia 279, sessão 0).
    Só remove o sufixo quando o dia do ano bate com a data do cabeçalho.
    """
    stem = path.stem.upper()
    if survey_date is not None:
        m = _SESSION_SUFFIX.match(stem)
        if m and int(m.group("doy")) == survey_date.timetuple().tm_yday:
            return m.group("name")
    return stem
