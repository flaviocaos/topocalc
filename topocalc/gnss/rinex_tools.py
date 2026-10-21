"""
rinex_tools.py
==============
Conversão de cada .GNS para RINEX 3.04 (observação + navegação), junção das
navegações de um dia e ajustes de cabeçalho (marco, altura da antena,
posição aproximada, receptor).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from . import rtklib
from .gns_file import FORMAT_NOVATEL, FORMAT_TRIMBLE, GnsHeader
from .novatel_eph import extract_gps_nav, write_nav_header
from .rt27 import write_rinex_obs

GPS_EPOCH = date(1980, 1, 6)


def gps_week(d: date) -> int:
    return (d - GPS_EPOCH).days // 7


def convert_gns(h: GnsHeader, outdir: Path, stem: str, marker: str,
                antenna_height: float) -> dict:
    """Gera <stem>.obs e <stem>.nav. Retorna caminhos e estatísticas."""
    outdir.mkdir(parents=True, exist_ok=True)
    obs = outdir / f"{stem}.obs"
    nav = outdir / f"{stem}.nav"
    info: dict = {"obs": obs, "proc_obs": obs, "nav": nav, "warnings": []}
    receiver = f"HI-TARGET {h.model_name or ''}".strip()

    if h.data_format == FORMAT_NOVATEL:
        tmpnav = outdir / f"{stem}_rtk.nav"
        rtklib.run_convbin(["-r", "nov", "-os", "-v", "3.04", "-hm", marker,
                            "-hd", "%.4f/0/0" % antenna_height,
                            "-hr", "%s/%s/" % (h.serial or "", receiver),
                            "-o", str(obs), "-n", str(tmpnav), str(h.path)])
        gps = extract_gps_nav(h.path)
        records = list(_nav_records(tmpnav)) + gps
        with open(nav, "w", newline="\n") as o:
            write_nav_header(o, f"NovAtel {h.path.name}")
            for r in records:
                o.write(r)
        tmpnav.unlink(missing_ok=True)
        if not gps:
            info["warnings"].append("Nenhuma efeméride GPS (GPSEPHEM) no arquivo.")
    elif h.data_format == FORMAT_TRIMBLE:
        r = write_rinex_obs(h.path, obs, marker, antenna_height, receiver, h.serial or "")
        info["epochs"] = r["epochs"]
        proc = outdir / f"{stem}_proc.obs"
        write_rinex_obs(h.path, proc, marker, antenna_height, receiver, h.serial or "", lli=False)
        info["proc_obs"] = proc
        week = gps_week(r["first"].date())
        rtklib.run_convbin(["-r", "rt17", "-ro", f"-WEEK={week}", "-v", "3.04",
                            "-n", str(nav), "-o", str(outdir / f"{stem}_rt17.obs"), str(h.path)])
        (outdir / f"{stem}_rt17.obs").unlink(missing_ok=True)
        if not nav.exists():
            with open(nav, "w") as o:
                write_nav_header(o)
    else:
        raise ValueError(f"{h.path.name}: formato interno não reconhecido.")

    if not obs.exists() or obs.stat().st_size < 2000:
        raise ValueError(f"{h.path.name}: a conversão para RINEX não produziu observações.")
    info.update(obs_summary(obs))
    return info


def _nav_records(path: Path) -> Iterable[str]:
    """Registros (texto completo, várias linhas) de um RINEX 3 de navegação."""
    if not path.exists():
        return
    lines = path.read_text(errors="ignore").splitlines(keepends=True)
    try:
        i = next(k for k, ln in enumerate(lines) if "END OF HEADER" in ln) + 1
    except StopIteration:
        return
    cur: list[str] = []
    for ln in lines[i:]:
        if ln[:1].isalpha():
            if cur:
                yield "".join(cur)
            cur = [ln]
        elif cur:
            cur.append(ln)
    if cur:
        yield "".join(cur)


def merge_nav(navs: Iterable[Path], out: Path) -> Path:
    """Junta navegações sem duplicar (chave = satélite + época de referência)."""
    seen: dict[str, str] = {}
    for n in navs:
        for rec in _nav_records(n):
            key = rec[:23]
            if key not in seen:
                seen[key] = rec
    with open(out, "w", newline="\n") as o:
        write_nav_header(o, "Navegacao combinada (todos os receptores do dia)")
        for k in sorted(seen, key=lambda s: (s[:1], s[4:23], s[1:3])):
            o.write(seen[k])
    return out


def obs_summary(obs: Path) -> dict:
    first = last = None
    n = 0
    interval = None
    prev = None
    with open(obs, errors="ignore") as f:
        for ln in f:
            if ln.startswith(">"):
                p = ln.split()
                try:
                    t = datetime(int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]),
                                 int(float(p[6])))
                except (ValueError, IndexError):
                    continue
                if first is None:
                    first = t
                if prev is not None and interval is None:
                    dt = (t - prev).total_seconds()
                    if dt > 0:
                        interval = dt
                prev = t
                last = t
                n += 1
    return {"first": first, "last": last, "n_epochs": n, "interval_s": interval}


def patch_header(obs: Path, approx_xyz: Optional[Iterable[float]] = None,
                 marker: Optional[str] = None, antenna_height: Optional[float] = None,
                 antenna_type: Optional[str] = None) -> None:
    lines = obs.read_text(errors="ignore").splitlines(keepends=True)
    for i, ln in enumerate(lines):
        lab = ln[60:].strip()
        if lab == "END OF HEADER":
            break
        if lab == "APPROX POSITION XYZ" and approx_xyz is not None:
            x, y, z = approx_xyz
            lines[i] = "%-60s%-20s\n" % ("%14.4f%14.4f%14.4f" % (x, y, z), "APPROX POSITION XYZ")
        elif lab == "MARKER NAME" and marker:
            lines[i] = "%-60s%-20s\n" % (marker[:60], "MARKER NAME")
        elif lab == "ANT # / TYPE" and antenna_type:
            t = antenna_type.split()
            name = "%-16s%-4s" % (t[0], t[1] if len(t) > 1 else "NONE")
            lines[i] = "%-60s%-20s\n" % ("%-20s%-20s" % ("", name), "ANT # / TYPE")
        elif lab == "ANTENNA: DELTA H/E/N" and antenna_height is not None:
            lines[i] = "%-60s%-20s\n" % ("%14.4f%14.4f%14.4f" % (antenna_height, 0, 0),
                                          "ANTENNA: DELTA H/E/N")
    obs.write_text("".join(lines))
