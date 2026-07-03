"""
pipeline.py
===========
Orquestra parsing -> cálculo geodésico, produzindo um único objeto de
resultado (ProcessingResult) consumido por todos os 4 geradores de saída.
Isso garante que docx, pdf, kml e xlsx usem exatamente os mesmos números.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .parser import GnsFileData, parse_many, detect_base
from .geo_calc import PointGeodesy, build_point_geodesy, apply_base_relations


@dataclass
class ProcessingResult:
    files: list[GnsFileData]
    base_file: GnsFileData
    point_files: list[GnsFileData]
    base_geo: PointGeodesy
    points_geo: list[PointGeodesy]
    single_zda_note: bool  # True se apenas 1 arquivo tem ZDA > 0

    def all_geo(self) -> list[PointGeodesy]:
        return [self.base_geo] + self.points_geo


def run_pipeline(paths: list[Path], forced_base_name: Optional[str] = None) -> ProcessingResult:
    if len(paths) < 2:
        raise ValueError("São necessários pelo menos 2 arquivos .GNS (1 base + 1 ponto).")

    files = parse_many(paths)
    files = detect_base(files, forced_base_name=forced_base_name)

    base_file = next(f for f in files if f.is_base)
    point_files = [f for f in files if not f.is_base]

    base_geo = build_point_geodesy(
        base_file.label, base_file.lat_rad, base_file.lon_rad, base_file.alt_m
    )
    points_geo = [
        build_point_geodesy(f.label, f.lat_rad, f.lon_rad, f.alt_m) for f in point_files
    ]
    apply_base_relations(base_geo, points_geo)

    files_with_zda = sum(1 for f in files if f.zda_count > 0)
    single_zda_note = files_with_zda <= 1

    return ProcessingResult(
        files=files,
        base_file=base_file,
        point_files=point_files,
        base_geo=base_geo,
        points_geo=points_geo,
        single_zda_note=single_zda_note,
    )
