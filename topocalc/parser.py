"""
parser.py
=========
Leitura de arquivos brutos .GNS (formato proprietário Hi-Target/Champion).

IMPORTANTE - AVISO DE ENGENHARIA REVERSA
-----------------------------------------
O formato .GNS não possui especificação pública do fabricante. O layout de
bytes usado aqui (offset 0x5A / 90 decimal contendo lat/lon/altitude) foi
determinado por engenharia reversa e validação manual cruzada com coordenadas
conhecidas, em arquivos gerados pelos modelos V90/HITV90 e D8 Pro/HITV200
(firmwares 90.0 e 100.0). NÃO há garantia de que o layout se mantenha em
outros modelos, firmwares ou versões de formato. Todo relatório gerado por
este pacote deve deixar essa limitação explícita.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


COORD_OFFSET = 0x5A  # 90 decimal — início dos campos lat/lon/alt
LAT_SIZE = 8   # float64 LE, radianos
LON_SIZE = 8   # float64 LE, radianos
ALT_SIZE = 4   # float32 LE, metros

# Janela de bytes do início do arquivo onde procuramos o cabeçalho ASCII.
HEADER_SCAN_WINDOW = 512

KNOWN_MODELS = ["V90", "HITV90", "D8 Pro", "D8Pro", "HITV200"]
KNOWN_FIRMWARES = ["90.0", "100.0"]

# NMEA ZDA: $--ZDA,hhmmss.ss,dd,mm,yyyy,xx,xx*hh
ZDA_REGEX = re.compile(
    rb"\$[A-Z]{2}ZDA,(\d{2})(\d{2})(\d{2})(?:\.\d+)?,(\d{2}),(\d{2}),(\d{4}),"
)


@dataclass
class GnsFileData:
    path: Path
    raw_bytes: bytes

    # Cabeçalho / metadados
    format_version: Optional[str] = None
    header_date: Optional[str] = None
    receiver_model: Optional[str] = None
    firmware_version: Optional[str] = None
    serial_id: Optional[str] = None

    # Detecção de base vs. rover
    zda_count: int = 0
    is_base: bool = False
    session_start_utc: Optional[datetime] = None
    session_end_utc: Optional[datetime] = None

    # Coordenadas cruas extraídas
    lat_rad: float = 0.0
    lon_rad: float = 0.0
    alt_m: float = 0.0

    # Evidência de auditoria (bytes crus em hex, para o relatório)
    audit_hex: str = ""

    # Rótulo lógico atribuído pelo pipeline ("BASE" ou nome do ponto)
    label: str = field(default="")

    def as_dict(self) -> dict:
        return {
            "arquivo": self.path.name,
            "versao_formato": self.format_version or "não identificada",
            "data_cabecalho": self.header_date or "não identificada",
            "modelo_receptor": self.receiver_model or "não identificado",
            "firmware": self.firmware_version or "não identificado",
            "serial_id": self.serial_id or "não identificado",
            "zda_count": self.zda_count,
            "is_base": self.is_base,
            "sessao_inicio_utc": self.session_start_utc.isoformat() if self.session_start_utc else None,
            "sessao_fim_utc": self.session_end_utc.isoformat() if self.session_end_utc else None,
        }


def _extract_ascii_header(raw: bytes) -> dict:
    """Faz uma extração best-effort de metadados textuais do cabeçalho.

    O cabeçalho ASCII do .GNS não segue um schema documentado; usamos
    heurísticas de regex sobre a janela inicial do arquivo.
    """
    window = raw[:HEADER_SCAN_WINDOW]
    text = window.decode("ascii", errors="ignore")

    result = {
        "format_version": None,
        "header_date": None,
        "receiver_model": None,
        "firmware_version": None,
        "serial_id": None,
    }

    m = re.search(r"(?:VER|VERSION)[\s:_-]*([0-9]+\.[0-9]+(?:\.[0-9]+)?)", text, re.I)
    if m:
        result["format_version"] = m.group(1)

    m = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", text)
    if m:
        result["header_date"] = m.group(1)

    for model in KNOWN_MODELS:
        if model.replace(" ", "").upper() in text.upper().replace(" ", ""):
            result["receiver_model"] = model
            break

    for fw in KNOWN_FIRMWARES:
        if fw in text:
            result["firmware_version"] = fw
            break
    if result["firmware_version"] is None:
        m = re.search(r"(?:FW|FIRMWARE)[\s:_-]*([0-9]+\.[0-9]+)", text, re.I)
        if m:
            result["firmware_version"] = m.group(1)

    m = re.search(r"(?:SN|SERIAL|ID)[\s:_-]*([A-Z0-9]{4,20})", text, re.I)
    if m:
        result["serial_id"] = m.group(1)

    return result


def _extract_zda_sessions(raw: bytes) -> tuple[int, Optional[datetime], Optional[datetime]]:
    """Conta sentenças ZDA e extrai timestamps de início/fim de sessão."""
    matches = list(ZDA_REGEX.finditer(raw))
    count = len(matches)
    if count == 0:
        return 0, None, None

    timestamps = []
    for m in matches:
        hh, mm, ss, day, month, year = m.groups()
        try:
            ts = datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss))
            timestamps.append(ts)
        except ValueError:
            continue

    if not timestamps:
        return count, None, None

    return count, min(timestamps), max(timestamps)


def _extract_coordinates(raw: bytes, path: Path) -> tuple[float, float, float, str]:
    end = COORD_OFFSET + LAT_SIZE + LON_SIZE + ALT_SIZE
    if len(raw) < end:
        raise ValueError(
            f"Arquivo '{path.name}' menor que o esperado para extração de "
            f"coordenadas (precisa de >= {end} bytes, tem {len(raw)})."
        )

    chunk = raw[COORD_OFFSET:end]
    lat_rad, lon_rad = struct.unpack_from("<dd", chunk, 0)
    (alt_m,) = struct.unpack_from("<f", chunk, LAT_SIZE + LON_SIZE)

    audit_hex = chunk.hex(" ")
    return lat_rad, lon_rad, alt_m, audit_hex


def parse_gns_file(path: Path) -> GnsFileData:
    raw = path.read_bytes()

    header = _extract_ascii_header(raw)
    zda_count, session_start, session_end = _extract_zda_sessions(raw)
    lat_rad, lon_rad, alt_m, audit_hex = _extract_coordinates(raw, path)

    data = GnsFileData(
        path=path,
        raw_bytes=raw,
        format_version=header["format_version"],
        header_date=header["header_date"],
        receiver_model=header["receiver_model"],
        firmware_version=header["firmware_version"],
        serial_id=header["serial_id"],
        zda_count=zda_count,
        session_start_utc=session_start,
        session_end_utc=session_end,
        lat_rad=lat_rad,
        lon_rad=lon_rad,
        alt_m=alt_m,
        audit_hex=audit_hex,
    )
    return data


def parse_many(paths: list[Path]) -> list[GnsFileData]:
    return [parse_gns_file(p) for p in paths]


def detect_base(files: list[GnsFileData], forced_base_name: Optional[str] = None) -> list[GnsFileData]:
    """Define qual arquivo é a BASE.

    Se forced_base_name for informado (nome de arquivo indicado pelo usuário),
    ele prevalece. Caso contrário, usa a heurística: maior contagem de
    sentenças ZDA no corpo do arquivo = base (estação contínua).
    """
    if forced_base_name:
        found = False
        for f in files:
            if f.path.name == forced_base_name:
                f.is_base = True
                f.label = "BASE"
                found = True
            else:
                f.is_base = False
        if not found:
            raise ValueError(
                f"Arquivo indicado como base '{forced_base_name}' não está "
                f"entre os arquivos fornecidos."
            )
    else:
        base_file = max(files, key=lambda f: f.zda_count)
        for f in files:
            f.is_base = f is base_file
            f.label = "BASE" if f.is_base else ""

    point_idx = 1
    for f in files:
        if not f.is_base:
            f.label = f"P{point_idx:02d}"
            point_idx += 1

    return files
