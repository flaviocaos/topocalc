"""
crs.py
======
Sistemas de coordenadas de saída.

Referencial: as coordenadas finais ficam no MESMO referencial das
coordenadas do controle (o processamento só mede vetores). Por isso o usuário
informa em que referencial o controle está — WGS 84, SIRGAS 2000 ou ITRF — e
isso define o rótulo e a projeção UTM de saída (WGS 84 e SIRGAS 2000 usam
elipsoides praticamente idênticos; a diferença entre eles está na realização,
não na fórmula).

Sistemas adicionais (opcionais):
* Camacupa 2015 / UTM (Angola) — transformação EPSG "Camacupa 2015 to WGS 84
  (11)", precisão declarada de ~3 m.
  Serve para localização e compatibilidade com cartografia oficial; não
  substitui parâmetros locais determinados em marcos da rede angolana.
  (Camacupa 1948 não é oferecido: o EPSG só tem transformações para blocos
  offshore; em terra o resultado seria errado em centenas de metros.)
* Plano topográfico local: plano tangente ao elipsoide na origem escolhida,
  com Este/Norte locais (falsa origem opcional, padrão 150 000 / 250 000 m) e
  a altitude ortométrica. Distâncias medidas no plano correspondem às do
  terreno na altitude da origem. Não é a formulação completa da NBR 14166.
"""

from __future__ import annotations

from functools import lru_cache



from .geodesy import enu_matrix, llh_to_ecef

FRAMES = {
    "WGS84": {"label": "WGS 84", "utm_epsg": lambda z, s: (32700 if s else 32600) + z},
    "SIRGAS2000": {"label": "SIRGAS 2000",
                   "utm_epsg": lambda z, s: (31960 + z) if s else (31954 + z) if 17 <= z <= 22 else None},
    "ITRF": {"label": "ITRF/IGS (época do levantamento)", "utm_epsg": lambda z, s: (32700 if s else 32600) + z},
}

EXTRA = {
    "camacupa2015": "Camacupa 2015 / UTM (Angola) — transformação ±3 m",
    "ltp": "Plano topográfico local (tangente na origem)",
}


def frame_label(frame: str) -> str:
    return FRAMES.get(frame, FRAMES["WGS84"])["label"]


@lru_cache(maxsize=16)
def _to_projected(geo_epsg: int, zone: int, south: bool):
    from pyproj import CRS, Transformer
    from pyproj.crs import ProjectedCRS
    from pyproj.crs.coordinate_operation import UTMConversion
    proj = ProjectedCRS(conversion=UTMConversion(zone, "S" if south else "N"),
                        geodetic_crs=CRS.from_epsg(geo_epsg))
    # área de interesse: Angola — ajuda o PROJ a escolher a transformação certa
    from pyproj.aoi import AreaOfInterest
    t = Transformer.from_crs(4326, proj, always_xy=True,
                             area_of_interest=AreaOfInterest(11.6, -18.1, 24.1, -4.3))
    t.transform(16.0, -10.0)
    if "ballpark" in (t.description or "").lower():
        raise ValueError("sem transformação oficial para esta região")
    return t


def camacupa_utm(lat: float, lon: float, zone: int, south: bool, which: str) -> tuple[float, float]:
    epsg = {"camacupa2015": 8694}[which]
    e, n = _to_projected(epsg, zone, south).transform(lon, lat)
    return float(e), float(n)


def ltp(points: list[dict], origin: dict, false_e: float = 150000.0, false_n: float = 250000.0) -> dict:
    """Plano topográfico local: {nome: (E_local, N_local, U)} a partir de lat/lon/h."""
    X0 = llh_to_ecef(origin["lat"], origin["lon"], origin["h"])
    R = enu_matrix(origin["lat"], origin["lon"])
    out = {}
    for p in points:
        d = R @ (llh_to_ecef(p["lat"], p["lon"], p["h"]) - X0)
        out[p["name"]] = (false_e + float(d[0]), false_n + float(d[1]), float(d[2]))
    return out


def add_extra(points: list[dict], extra: list[str], zone: int, south: bool,
              ltp_origin: str | None = None) -> dict:
    """Acrescenta as coordenadas nos sistemas extras em cada ponto (campo 'extra')
    e devolve a descrição dos sistemas usados."""
    info = {}
    for key in extra:
        if key == "camacupa2015":
            try:
                for p in points:
                    p.setdefault("extra", {})[key] = camacupa_utm(p["lat"], p["lon"], zone, south, key)
                info[key] = f"{EXTRA[key].split(' —')[0]} fuso {zone}{'S' if south else 'N'}"
            except Exception as e:  # noqa: BLE001
                info[key] = f"indisponível: {e}"
        elif key == "ltp" and points:
            origin = next((p for p in points if p["name"] == ltp_origin), None) or points[0]
            vals = ltp(points, origin)
            for p in points:
                e, n, u = vals[p["name"]]
                p.setdefault("extra", {})["ltp"] = (e, n, u)
            info["ltp"] = (f"Plano topográfico local, origem {origin['name']} "
                           f"(E 150 000 m, N 250 000 m), plano tangente na altitude h = {origin['h']:.3f} m")
    return info


__all__ = ["FRAMES", "EXTRA", "frame_label", "add_extra", "camacupa_utm", "ltp"]

