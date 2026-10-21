"""CSV das coordenadas finais (separador ';', decimal '.')."""

from __future__ import annotations

from pathlib import Path


def export_csv(p: dict, path: Path) -> Path:
    pts = p["results"]["points"]
    extra = [k for k, v in sorted((p["results"].get("extra_crs") or {}).items()) if not v.startswith("indisp")]
    names = {"camacupa2015": "Camacupa2015 UTM", "ltp": "Plano local"}
    lines = ["PONTO;E (m);N (m);FUSO;h elipsoidal (m);N geoidal (m);H ortometrica (m);"
             "Latitude;Longitude;sigma E (m);sigma N (m);sigma h (m);Tipo;Vetores fixos;Vetores flutuantes"
             + "".join(f";{names.get(k, k)} E;{names.get(k, k)} N" for k in extra)]
    for pt in pts:
        lines.append(";".join([
            pt["name"], f"{pt['E']:.3f}", f"{pt['N']:.3f}", f"{pt['zone']}{pt['hemisphere']}",
            f"{pt['h']:.3f}", f"{pt['N_geoid']:.3f}", f"{pt['H']:.3f}",
            f"{pt['lat']:.9f}", f"{pt['lon']:.9f}",
            f"{pt['sE']:.3f}", f"{pt['sN']:.3f}", f"{pt['sU']:.3f}",
            "Controle (fixo)" if pt["fixed"] else ("Controle" if pt["type"] == "controle" else "Ajustado"),
            str(pt["vectors"]["fixa"]), str(pt["vectors"]["flutuante"]),
        ] + [f"{(pt.get('extra') or {}).get(k, (float('nan'), 0))[i]:.3f}" for k in extra for i in (0, 1)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path
