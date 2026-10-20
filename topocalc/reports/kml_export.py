"""KML com os pontos ajustados e as linhas de base (cor por tipo de solução)."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from .common import baseline_rows

STYLES = """
<Style id="ctrl"><IconStyle><color>ff0000ff</color><scale>1.2</scale>
<Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon></IconStyle></Style>
<Style id="pt"><IconStyle><color>ff938a1e</color><scale>1.0</scale>
<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>
<Style id="fixa"><LineStyle><color>ff00aa00</color><width>2.5</width></LineStyle></Style>
<Style id="flutuante"><LineStyle><color>ff00a5ff</color><width>2.5</width></LineStyle></Style>
<Style id="excluida"><LineStyle><color>ff0000ff</color><width>1.5</width></LineStyle></Style>
"""


def export_kml(p: dict, path: Path) -> Path:
    pts = {pt["name"]: pt for pt in p["results"]["points"]}
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{escape(p.get('title') or 'Levantamento GNSS')}</name>", STYLES,
           "<Folder><name>Pontos</name>"]
    for pt in pts.values():
        desc = (f"E {pt['E']:.3f} | N {pt['N']:.3f} | fuso {pt['zone']}{pt['hemisphere']}<br/>"
                f"h {pt['h']:.3f} m | H {pt['H']:.3f} m<br/>"
                f"σE {pt['sE'] * 1000:.0f} mm, σN {pt['sN'] * 1000:.0f} mm, σh {pt['sU'] * 1000:.0f} mm")
        out.append(f"<Placemark><name>{escape(pt['name'])}</name>"
                   f"<description><![CDATA[{desc}]]></description>"
                   f"<styleUrl>#{'ctrl' if pt['type'] == 'controle' else 'pt'}</styleUrl>"
                   f"<Point><coordinates>{pt['lon']:.9f},{pt['lat']:.9f},{pt['h']:.3f}</coordinates></Point>"
                   "</Placemark>")
    out.append("</Folder><Folder><name>Linhas de base</name>")
    for r in baseline_rows(p):
        a, b = pts.get(r["base"]), pts.get(r["rover"])
        if not a or not b:
            continue
        style = "excluida" if r["excluded"] else r["solution"]
        desc = (f"{r['day']} — {r['solution']} — {r['length_m'] / 1000:.3f} km — "
                f"{r['duration_min']:.0f} min" + (" — EXCLUÍDA do ajustamento" if r["excluded"] else ""))
        out.append(f"<Placemark><name>{escape(r['base'])} → {escape(r['rover'])}</name>"
                   f"<description>{escape(desc)}</description><styleUrl>#{style}</styleUrl>"
                   f"<LineString><tessellate>1</tessellate><coordinates>"
                   f"{a['lon']:.9f},{a['lat']:.9f},0 {b['lon']:.9f},{b['lat']:.9f},0"
                   "</coordinates></LineString></Placemark>")
    out.append("</Folder></Document></kml>")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
