"""
novatel_eph.py
==============
Extrai efemérides GPS (log NovAtel GPSEPHEM, id 7) dos arquivos .GNS com
placa NovAtel e grava RINEX 3.04 de navegação.

O RTKLIB (convbin -r nov) converte as observações e as efemérides GLONASS e
Galileo desses arquivos, mas ignora o GPSEPHEM; sem este módulo, um projeto
só com receptores NovAtel ficaria sem navegação GPS.

Layout do corpo GPSEPHEM (224 bytes, little-endian):
  0 PRN u32 | 4 tow f64 | 12 saúde u32 | 16 IODE1 | 20 IODE2 | 24 semana |
  28 z-semana | 32 toe | 40 A | 48 dN | 56 M0 | 64 e | 72 ω | 80 cuc |
  88 cus | 96 crc | 104 crs | 112 cic | 120 cis | 128 i0 | 136 IDOT |
  144 Ω0 | 152 Ωdot | 160 IODC u32 | 164 toc | 172 tgd | 180 af0 |
  188 af1 | 196 af2 | 204 AS u32 | 208 N | 216 variância URA (m²)
"""

from __future__ import annotations

import math
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .gns_file import DATA_OFFSET

SYNC = b"\xaa\x44\x12"
MSG_GPSEPHEM = 7
GPS_EPOCH = datetime(1980, 1, 6)


def iter_gpsephem(d: bytes, start: int = DATA_OFFSET):
    i = start
    n = len(d)
    while True:
        i = d.find(SYNC, i)
        if i < 0 or i + 28 > n:
            return
        hl = d[i + 3]
        mid, = struct.unpack_from("<H", d, i + 4)
        ml, = struct.unpack_from("<H", d, i + 8)
        if hl < 28 or i + hl + ml + 4 > n:
            i += 3
            continue
        if mid == MSG_GPSEPHEM and ml == 224:
            body = d[i + hl:i + hl + ml]
            # Sem verificar CRC: o RINEX final é conferido pela consistência
            # física dos parâmetros (a, e, semana) abaixo.
            yield body
        i += hl + ml + 4


def _decode(b: bytes) -> dict | None:
    prn, tow, health, iode1, iode2, week, zweek = struct.unpack_from("<IdIIIII", b, 0)
    (toe, A, dN, M0, ecc, w, cuc, cus, crc, crs, cic, cis, i0, idot, OMG0,
     OMGd) = struct.unpack_from("<16d", b, 32)
    iodc, = struct.unpack_from("<I", b, 160)
    toc, tgd, af0, af1, af2 = struct.unpack_from("<5d", b, 164)
    as_flag, = struct.unpack_from("<I", b, 204)
    N, ura_var = struct.unpack_from("<dd", b, 208)
    if not (1 <= prn <= 32) or not (2.5e7 < A < 2.8e7) or not (0 <= ecc < 0.1):
        return None
    if not (1000 < week < 5000) or not (0 <= toe < 604800) or not (0 <= toc < 604800):
        return None
    return dict(prn=prn, tow=tow, health=health, iode=iode1, week=week, toe=toe,
                sqrtA=math.sqrt(A), dN=dN, M0=M0, e=ecc, w=w, cuc=cuc, cus=cus,
                crc=crc, crs=crs, cic=cic, cis=cis, i0=i0, idot=idot, OMG0=OMG0,
                OMGd=OMGd, iodc=iodc, toc=toc, tgd=tgd, af0=af0, af1=af1, af2=af2,
                ura=math.sqrt(ura_var) if ura_var > 0 else 0.0)


def _f(x: float) -> str:
    return "%19.12E" % x


def format_record(e: dict) -> str:
    t = GPS_EPOCH + timedelta(weeks=e["week"], seconds=e["toc"])
    # toc pode estar na semana seguinte/anterior à de referência
    if e["toc"] - e["toe"] > 302400:
        t -= timedelta(weeks=1)
    elif e["toc"] - e["toe"] < -302400:
        t += timedelta(weeks=1)
    tx = e["tow"]
    lines = [
        "G%02d %04d %02d %02d %02d %02d %02d" % (e["prn"], t.year, t.month, t.day, t.hour, t.minute, t.second)
        + _f(e["af0"]) + _f(e["af1"]) + _f(e["af2"]),
        "    " + _f(e["iode"]) + _f(e["crs"]) + _f(e["dN"]) + _f(e["M0"]),
        "    " + _f(e["cuc"]) + _f(e["e"]) + _f(e["cus"]) + _f(e["sqrtA"]),
        "    " + _f(e["toe"]) + _f(e["cic"]) + _f(e["OMG0"]) + _f(e["cis"]),
        "    " + _f(e["i0"]) + _f(e["crc"]) + _f(e["w"]) + _f(e["OMGd"]),
        "    " + _f(e["idot"]) + _f(1.0) + _f(e["week"]) + _f(0.0),
        "    " + _f(e["ura"]) + _f(e["health"]) + _f(e["tgd"]) + _f(e["iodc"]),
        "    " + _f(tx) + _f(4.0),
    ]
    return "\n".join(lines) + "\n"


def extract_gps_nav(gns_path: Path) -> list[str]:
    """Lista de registros RINEX 3 (texto) das efemérides GPS distintas."""
    d = gns_path.read_bytes()
    seen = {}
    for body in iter_gpsephem(d):
        e = _decode(body)
        if not e:
            continue
        key = (e["prn"], e["week"], round(e["toe"]), e["iode"])
        if key not in seen:
            seen[key] = format_record(e)
    return [seen[k] for k in sorted(seen)]


def write_nav_header(o, comment: str = "") -> None:
    o.write("%-60s%-20s\n" % ("     3.04           N: GNSS NAV DATA    M: Mixed", "RINEX VERSION / TYPE"))
    o.write("%-60s%-20s\n" % ("%-20s%-20s%s" % ("TopoCalc", "",
                              datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S UTC")), "PGM / RUN BY / DATE"))
    if comment:
        o.write("%-60s%-20s\n" % (comment[:60], "COMMENT"))
    o.write("%-60s%-20s\n" % ("", "END OF HEADER"))
