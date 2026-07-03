"""
kml_export.py
=============
Gera um KML com:
- 1 Placemark por ponto (estilo diferente para a base)
- descrição HTML com todos os atributos
- LineStrings conectando a base a cada ponto levantado
"""

from __future__ import annotations

from pathlib import Path

import simplekml

from .pipeline import ProcessingResult


def _description_html(p, is_base: bool) -> str:
    rows = [
        ("Latitude (graus)", f"{p.lat_deg:.8f}"),
        ("Longitude (graus)", f"{p.lon_deg:.8f}"),
        ("Latitude (DMS)", p.lat_dms),
        ("Longitude (DMS)", p.lon_dms),
        ("Altitude elipsoidal (m)", f"{p.alt_m:.3f}"),
        ("UTM zona", f"{p.utm_zone}{p.utm_hemisphere}"),
        ("UTM Este (m)", f"{p.utm_easting:.3f}"),
        ("UTM Norte (m)", f"{p.utm_northing:.3f}"),
    ]
    if not is_base:
        rows += [
            ("Delta E (m)", f"{p.delta_e:.3f}"),
            ("Delta N (m)", f"{p.delta_n:.3f}"),
            ("Delta h (m)", f"{p.delta_h:.3f}"),
            ("Distância plana (m)", f"{p.dist_plana_m:.3f}"),
            ("Distância geodésica (m)", f"{p.dist_geodesica_m:.3f}"),
            ("Azimute (base->ponto, graus)", f"{p.azimute_deg:.4f}"),
        ]

    html_rows = "".join(
        f"<tr><td><b>{label}</b></td><td>{value}</td></tr>" for label, value in rows
    )
    return f"<table border='1' cellpadding='4' cellspacing='0'>{html_rows}</table>"


def export_kml(result: ProcessingResult, output_path: Path) -> Path:
    kml = simplekml.Kml(name="TopoCalc - Pacote Geodésico")

    base_style = simplekml.Style()
    base_style.iconstyle.color = simplekml.Color.red
    base_style.iconstyle.scale = 1.3
    base_style.iconstyle.icon.href = (
        "http://maps.google.com/mapfiles/kml/shapes/triangle.png"
    )

    point_style = simplekml.Style()
    point_style.iconstyle.color = simplekml.Color.yellow
    point_style.iconstyle.scale = 1.0
    point_style.iconstyle.icon.href = (
        "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    )

    base = result.base_geo
    pnt = kml.newpoint(
        name=f"{base.label} (BASE)",
        coords=[(base.lon_deg, base.lat_deg, base.alt_m)],
    )
    pnt.altitudemode = simplekml.AltitudeMode.absolute
    pnt.style = base_style
    pnt.description = _description_html(base, is_base=True)

    lines_folder = kml.newfolder(name="Linhas Base-Ponto")
    points_folder = kml.newfolder(name="Pontos Levantados")

    for p in result.points_geo:
        pt = points_folder.newpoint(
            name=p.label,
            coords=[(p.lon_deg, p.lat_deg, p.alt_m)],
        )
        pt.altitudemode = simplekml.AltitudeMode.absolute
        pt.style = point_style
        pt.description = _description_html(p, is_base=False)

        line = lines_folder.newlinestring(
            name=f"{base.label} -> {p.label}",
            coords=[
                (base.lon_deg, base.lat_deg, base.alt_m),
                (p.lon_deg, p.lat_deg, p.alt_m),
            ],
        )
        line.altitudemode = simplekml.AltitudeMode.absolute
        line.style.linestyle.color = simplekml.Color.blue
        line.style.linestyle.width = 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    kml.save(str(output_path))
    return output_path
