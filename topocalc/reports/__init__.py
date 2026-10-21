"""Entregáveis do projeto: relatório (docx/pdf), planilha, KML, CSV e RINEX."""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..gnss import project as P
from .common import slug
from .csv_export import export_csv
from .docx_report import build_docx
from .kml_export import export_kml
from .xlsx_export import export_xlsx


def build_all(pdir: Path) -> list[Path]:
    p = P.load(pdir)
    out = pdir / "output"
    out.mkdir(exist_ok=True)
    for f in out.glob("*"):
        if f.is_file():
            f.unlink()
    base = slug(p.get("title") or "levantamento")
    files: list[Path] = []
    files.append(export_csv(p, out / f"coordenadas_{base}.csv"))
    files.append(export_xlsx(p, out / f"planilha_{base}.xlsx"))
    files.append(export_kml(p, out / f"rede_{base}.kml"))
    docx = build_docx(p, out / f"relatorio_{base}.docx", pdir / "work")
    files.append(docx)
    try:
        from .pdf import convert_docx_to_pdf
        files.append(convert_docx_to_pdf(docx, out))
    except Exception as e:  # noqa: BLE001 — o PDF é opcional (depende do LibreOffice)
        p = P.load(pdir)
        p.setdefault("log", []).append(f"PDF não gerado: {e}")
        P.save(pdir, p)
    files.append(export_rinex_zip(p, out / f"RINEX_{base}.zip"))
    pack = out / f"pacote_{base}.zip"
    with zipfile.ZipFile(pack, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)
    files.append(pack)
    return files


def export_rinex_zip(p: dict, path: Path) -> Path:
    import numpy as np
    from ..gnss.geodesy import ecef_to_llh, enu_matrix
    from ..gnss.rinex_tools import patch_header
    final = {pt["name"]: pt for pt in (p.get("results") or {}).get("points", [])}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for o in p["occupations"]:
            if not o.get("include", True):
                continue
            day = o["day"]
            mark = None
            if o["name"] in final:
                from ..gnss.geodesy import llh_to_ecef
                f = final[o["name"]]
                mark = llh_to_ecef(f["lat"], f["lon"], f["h"])
            elif o.get("approx_xyz"):
                mark = np.array(o["approx_xyz"])
            arp = None
            if mark is not None:
                lat, lon, _ = ecef_to_llh(*mark)
                arp = mark + enu_matrix(lat, lon)[2] * float(o["antenna_height"])
            patch_header(Path(o["obs"]), approx_xyz=arp, marker=o["name"],
                         antenna_height=float(o["antenna_height"]),
                         antenna_type=o.get("antenna_type") or None)
            z.write(o["obs"], arcname=f"{day}/{o['name']}_{o['id']}.obs")
        for day, nav in (p.get("navs") or {}).items():
            z.write(nav, arcname=f"{day}/navegacao_{day}.nav")
    return path
