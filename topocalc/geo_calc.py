"""
geo_calc.py
===========
Conversões e cálculos geodésicos, usando pyproj/PROJ para toda a matemática
de projeção e geodésia (nenhuma reimplementação manual de projeção).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from pyproj import Geod, Transformer, CRS

WGS84_GEOD = Geod(ellps="WGS84")


def rad_to_deg(rad: float) -> float:
    return math.degrees(rad)


def deg_to_dms(dec_deg: float, is_lat: bool) -> str:
    """Converte grau decimal -> string DMS com hemisfério.

    Correção de bug conhecido: usar int() diretamente em valores negativos
    trunca em direção a zero de forma inconsistente ao separar graus/min/seg
    (ex.: -23.5 -> int(-23.5) = -23, perdendo a fração corretamente, mas
    minutos/segundos calculados a partir do valor absoluto residual ficavam
    com sinal trocado em alguns casos de arredondamento). Aqui trabalhamos
    sempre com o valor ABSOLUTO e aplicamos o hemisfério separadamente.
    """
    hemi = ("N" if dec_deg >= 0 else "S") if is_lat else ("E" if dec_deg >= 0 else "W")
    abs_deg = abs(dec_deg)

    degrees = int(abs_deg)
    minutes_full = (abs_deg - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60

    # Corrige o caso de arredondamento em que seconds "vira" 60.0
    seconds = round(seconds, 3)
    if seconds >= 60.0:
        seconds -= 60.0
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        degrees += 1

    return f"{degrees:03d}°{minutes:02d}'{seconds:06.3f}\"{hemi}"


def utm_zone_from_lon(lon_deg: float) -> int:
    """Calcula a zona UTM correta a partir da longitude (não fixa a zona)."""
    return int(math.floor((lon_deg + 180) / 6) % 60) + 1


def utm_epsg(lat_deg: float, lon_deg: float) -> tuple[int, str, int]:
    """Retorna (código_epsg, hemisfério, zona) para a projeção UTM/WGS84
    adequada ao ponto informado."""
    zone = utm_zone_from_lon(lon_deg)
    if lat_deg >= 0:
        epsg = 32600 + zone
        hemi = "N"
    else:
        epsg = 32700 + zone
        hemi = "S"
    return epsg, hemi, zone


@dataclass
class PointGeodesy:
    label: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    lat_dms: str
    lon_dms: str
    utm_epsg: int
    utm_zone: int
    utm_hemisphere: str
    utm_easting: float
    utm_northing: float

    # Relação com a base (None para a própria base)
    delta_e: Optional[float] = None
    delta_n: Optional[float] = None
    delta_h: Optional[float] = None
    dist_plana_m: Optional[float] = None
    dist_geodesica_m: Optional[float] = None
    azimute_deg: Optional[float] = None

    # Preenchido se este ponto caiu num fuso UTM diferente do da BASE e teve
    # que ser reprojetado para o fuso da BASE antes do cálculo de deltas
    # (necessário porque coordenadas UTM de fusos diferentes não são
    # diretamente subtraíveis — cada fuso tem seu próprio meridiano central).
    reprojected_to_base_zone: bool = False
    native_utm_zone: Optional[int] = None
    native_utm_hemisphere: Optional[str] = None
    native_utm_easting: Optional[float] = None
    native_utm_northing: Optional[float] = None


def compute_utm(lat_deg: float, lon_deg: float) -> tuple[int, str, int, float, float]:
    epsg, hemi, zone = utm_epsg(lat_deg, lon_deg)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    easting, northing = transformer.transform(lon_deg, lat_deg)
    return epsg, hemi, zone, easting, northing


def geodetic_distance_azimuth(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> tuple[float, float]:
    """Distância geodésica elipsoidal (Karney, via pyproj.Geod) e azimute
    direto (base -> ponto), em graus (0-360)."""
    az12, _az21, dist = WGS84_GEOD.inv(lon1, lat1, lon2, lat2)
    az12 = az12 % 360
    return dist, az12


def build_point_geodesy(label: str, lat_rad: float, lon_rad: float, alt_m: float) -> PointGeodesy:
    lat_deg = rad_to_deg(lat_rad)
    lon_deg = rad_to_deg(lon_rad)
    epsg, hemi, zone, easting, northing = compute_utm(lat_deg, lon_deg)

    return PointGeodesy(
        label=label,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        alt_m=alt_m,
        lat_dms=deg_to_dms(lat_deg, is_lat=True),
        lon_dms=deg_to_dms(lon_deg, is_lat=False),
        utm_epsg=epsg,
        utm_zone=zone,
        utm_hemisphere=hemi,
        utm_easting=easting,
        utm_northing=northing,
    )


def reproject_to_zone(lat_deg: float, lon_deg: float, target_epsg: int) -> tuple[float, float]:
    """Reprojeta um ponto lat/lon para um EPSG UTM específico (não necessariamente
    o fuso "natural" desse ponto). Usado para trazer pontos de fusos vizinhos
    para o mesmo sistema de coordenadas da BASE antes do cálculo de deltas."""
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{target_epsg}", always_xy=True)
    easting, northing = transformer.transform(lon_deg, lat_deg)
    return easting, northing


def apply_base_relations(base: PointGeodesy, points: list[PointGeodesy]) -> None:
    """Preenche delta E/N/h, distância plana, distância geodésica e azimute
    de cada ponto em relação à base (in place).

    Se um ponto cair num fuso UTM diferente do da BASE, ele é reprojetado
    para o fuso da BASE antes do cálculo de delta/distância plana — caso
    contrário a subtração de Este/Norte misturaria dois sistemas de
    coordenadas diferentes e produziria um resultado sem sentido. A
    distância geodésica e o azimute (via pyproj.Geod, direto sobre o
    elipsoide) não são afetados por isso em nenhum caso — servem inclusive
    como referência cruzada para detectar esse tipo de erro.
    """
    for p in points:
        if p.utm_zone != base.utm_zone or p.utm_hemisphere != base.utm_hemisphere:
            p.native_utm_zone = p.utm_zone
            p.native_utm_hemisphere = p.utm_hemisphere
            p.native_utm_easting = p.utm_easting
            p.native_utm_northing = p.utm_northing

            new_easting, new_northing = reproject_to_zone(p.lat_deg, p.lon_deg, base.utm_epsg)
            p.utm_zone = base.utm_zone
            p.utm_hemisphere = base.utm_hemisphere
            p.utm_epsg = base.utm_epsg
            p.utm_easting = new_easting
            p.utm_northing = new_northing
            p.reprojected_to_base_zone = True

        p.delta_e = p.utm_easting - base.utm_easting
        p.delta_n = p.utm_northing - base.utm_northing
        p.delta_h = p.alt_m - base.alt_m
        p.dist_plana_m = math.hypot(p.delta_e, p.delta_n)

        dist_geo, az = geodetic_distance_azimuth(
            base.lat_deg, base.lon_deg, p.lat_deg, p.lon_deg
        )
        p.dist_geodesica_m = dist_geo
        p.azimute_deg = az
