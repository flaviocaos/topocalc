"""Formatação comum aos entregáveis."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s or "levantamento"


def dms(value: float, kind: str) -> str:
    hemi = ("N" if value >= 0 else "S") if kind == "lat" else ("E" if value >= 0 else "W")
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    if s >= 59.99995:
        s = 0
        m += 1
    if m == 60:
        m = 0
        d += 1
    return f"{d}°{m:02d}'{s:08.5f}\" {hemi}"


def fmt_dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.strftime("%d/%m/%Y %H:%M")


def fmt_day(v) -> str:
    if not v:
        return ""
    return datetime.fromisoformat(str(v)[:10]).strftime("%d/%m/%Y")


def fmt_hm(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.strftime("%H:%M")


SOLUTION_LABEL = {"fixa": "Fixa", "flutuante": "Flutuante", "falhou": "Falhou"}


def baseline_rows(p: dict) -> list[dict]:
    """Linhas de base com resultado + resíduos do ajustamento, em ordem de processamento."""
    obs = {o["id"]: o for o in (p.get("results") or {}).get("observations", [])}
    rows = []
    for b in p["baselines"]:
        r = b.get("result")
        if not r:
            continue
        o = obs.get(r["id"])
        rows.append({**r, "excluded": bool(b.get("excluded")), "adj": o, "overlap_min": b["overlap_min"]})
    return rows
