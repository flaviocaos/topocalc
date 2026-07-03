"""
make_sample_gns.py
===================
Gera arquivos .GNS SINTÉTICOS (não são arquivos reais de receptor) apenas
para permitir testar o pipeline ponta a ponta sem depender de hardware.
Segue o layout assumido pelo parser: cabeçalho ASCII + payload binário a
partir do offset 0x5A (lat f64 LE rad, lon f64 LE rad, alt f32 LE m).
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path


def make_header(model: str, firmware: str, date: str, serial: str, n_zda: int) -> bytes:
    lines = [
        f"HITARGET GNS FORMAT VER:1.2",
        f"DATE:{date}",
        f"MODEL:{model}",
        f"FW:{firmware}",
        f"SN:{serial}",
    ]
    zda_lines = []
    hh = 12
    for i in range(n_zda):
        mm = i % 60
        zda_lines.append(f"$GPZDA,{hh:02d}{mm:02d}00.00,03,07,2026,00,00*4E")
    text = "\n".join(lines + zda_lines) + "\n"
    header = text.encode("ascii")
    if len(header) < 0x5A:
        header += b"\x00" * (0x5A - len(header))
    else:
        header = header[:0x5A]
    return header


def make_gns_file(path: Path, lat_deg: float, lon_deg: float, alt_m: float,
                   model: str, firmware: str, date: str, serial: str, n_zda: int) -> None:
    header = make_header(model, firmware, date, serial, n_zda)
    payload = struct.pack("<ddf", math.radians(lat_deg), math.radians(lon_deg), alt_m)
    path.write_bytes(header + payload + b"\x00" * 32)


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./amostras_gns")
    out_dir.mkdir(parents=True, exist_ok=True)

    # BASE: estação contínua, muitas sentenças ZDA
    make_gns_file(
        out_dir / "estacao_base.gns",
        lat_deg=-23.55052, lon_deg=-46.63331, alt_m=760.500,
        model="D8 Pro", firmware="100.0", date="2026-07-03", serial="D8P00123", n_zda=40,
    )

    # Pontos levantados (rover), sem ZDA
    pontos = [
        ("ponto_01.gns", -23.55100, -46.63280, 758.200),
        ("ponto_02.gns", -23.54990, -46.63400, 762.100),
        ("ponto_03.gns", -23.55210, -46.63510, 759.850),
    ]
    for name, lat, lon, alt in pontos:
        make_gns_file(
            out_dir / name, lat_deg=lat, lon_deg=lon, alt_m=alt,
            model="V90", firmware="90.0", date="2026-07-03", serial="V9000456", n_zda=0,
        )

    print(f"Arquivos de amostra gerados em: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
