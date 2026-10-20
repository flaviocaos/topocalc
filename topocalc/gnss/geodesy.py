"""
geodesy.py
==========
Conversões geodésicas básicas em WGS84 (ECEF <-> geodésicas, ENU, UTM) e
ondulação geoidal EGM96 embutida (grade 15', interpolação bilinear).
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)


def llh_to_ecef(lat_deg: float, lon_deg: float, h: float) -> np.ndarray:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    return np.array([(n + h) * math.cos(la) * math.cos(lo),
                     (n + h) * math.cos(la) * math.sin(lo),
                     (n * (1 - E2) + h) * math.sin(la)])


def ecef_to_llh(x: float, y: float, z: float) -> tuple[float, float, float]:
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - E2))
    for _ in range(10):
        n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat_new = math.atan2(z, p * (1 - E2 * n / (n + h)))
        if abs(lat_new - lat) < 1e-13:
            lat = lat_new
            break
        lat = lat_new
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def enu_matrix(lat_deg: float, lon_deg: float) -> np.ndarray:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return np.array([[-so, co, 0.0],
                     [-sl * co, -sl * so, cl],
                     [cl * co, cl * so, sl]])


def utm_zone(lon_deg: float) -> int:
    return int((lon_deg + 180) // 6) + 1


def to_utm(lat_deg: float, lon_deg: float, zone: int | None = None, south: bool | None = None):
    """Retorna (E, N, zona, hemisfério 'S'/'N', convergência °, fator de escala)."""
    from pyproj import CRS, Proj
    zone = zone or utm_zone(lon_deg)
    south = (lat_deg < 0) if south is None else south
    epsg = (32700 if south else 32600) + zone
    tr = _transformer(epsg)
    e, n = tr.transform(lon_deg, lat_deg)
    fac = Proj(CRS.from_epsg(epsg)).get_factors(lon_deg, lat_deg)
    return e, n, zone, "S" if south else "N", fac.meridian_convergence, fac.meridional_scale


def from_utm(e: float, n: float, zone: int, south: bool = True) -> tuple[float, float]:
    from pyproj import Transformer
    epsg = (32700 if south else 32600) + zone
    tr = Transformer.from_crs(epsg, 4326, always_xy=True)
    lon, lat = tr.transform(e, n)
    return lat, lon


@lru_cache(maxsize=8)
def _transformer(epsg: int):
    from pyproj import Transformer
    return Transformer.from_crs(4326, epsg, always_xy=True)


# --------------------------------------------------------------- geoide

_GEOID_FILE = Path(__file__).resolve().parent.parent / "data" / "egm96_15min.npz"


@lru_cache(maxsize=1)
def _egm96() -> np.ndarray:
    with np.load(_GEOID_FILE) as z:
        return z[z.files[0]]


def egm96(lat_deg: float, lon_deg: float) -> float:
    """Ondulação geoidal EGM96 (m), grade 15' com interpolação bilinear."""
    g = _egm96()
    nrow, ncol = g.shape  # 721 x 1440, linha 0 = 90°N, coluna 0 = 0°E
    step = 180.0 / (nrow - 1)
    r = (90.0 - lat_deg) / step
    c = (lon_deg % 360.0) / step
    r0 = min(int(math.floor(r)), nrow - 2)
    c0 = int(math.floor(c)) % ncol
    c1 = (c0 + 1) % ncol
    fr, fc = r - r0, c - math.floor(c)
    v = (g[r0, c0] * (1 - fr) * (1 - fc) + g[r0, c1] * (1 - fr) * fc
         + g[r0 + 1, c0] * fr * (1 - fc) + g[r0 + 1, c1] * fr * fc)
    return float(v) / 100.0
