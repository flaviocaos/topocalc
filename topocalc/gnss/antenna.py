"""
antenna.py
==========
Calibração de antenas (arquivos ANTEX).

* Satélites: extrato do igs20.atx embutido no TopoCalc (satélites ativos e
  antenas Hi-Target/CHC calibradas pelo IGS). Necessário para o PPP.
* Receptores: o igs20.atx quase não tem antenas de receptores de topografia.
  As calibrações da NGS (ngs20.atx) cobrem muito mais modelos; o TopoCalc tenta
  baixá-las uma vez (cache) e também aceita um ANTEX enviado pelo usuário.

A antena de cada arquivo é escolhida na revisão. Sem antena escolhida, nenhuma
correção de centro de fase é aplicada (comportamento anterior).

Atenção: para comparar com outro programa (ex.: Leica Infinity), use o mesmo
modelo de antena nos dois; com antenas diferentes as alturas diferem em
centímetros.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from .orbits import USER_AGENT, cache_dir as _orbit_cache

DATA = Path(__file__).resolve().parent.parent / "data"
BUILTIN = DATA / "igs20_topocalc.atx.gz"
NGS_URLS = [
    "https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx",
    "https://www.ngs.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx",
]


def _cache() -> Path:
    d = _orbit_cache().parent / "antex"
    d.mkdir(parents=True, exist_ok=True)
    return d


def builtin_atx() -> Path:
    """igs20 embutido, descompactado no cache."""
    out = _cache() / "igs20_topocalc.atx"
    if not out.exists() or out.stat().st_mtime < BUILTIN.stat().st_mtime:
        tmp = out.with_suffix(f".{os.getpid()}.tmp")
        with gzip.open(BUILTIN, "rt") as fi, open(tmp, "w") as fo:
            fo.write(fi.read())
        os.replace(tmp, out)
    return out


def ngs_atx(download: bool = True) -> Optional[Path]:
    """ngs20.atx (calibrações da NGS) — baixa uma vez e guarda em cache."""
    out = _cache() / "ngs20.atx"
    if out.exists() and out.stat().st_size > 100_000:
        return out
    if not download or os.environ.get("TOPOCALC_OFFLINE"):
        return None
    for url in NGS_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
        except OSError:
            continue
        if b"ANTEX VERSION" in data[:200] and len(data) > 100_000:
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, out)
            return out
    return None


def _blocks(path: Path):
    """Blocos (lista de linhas) de cada antena do ANTEX."""
    cur = None
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="ignore") as f:
        for ln in f:
            lab = ln[60:].strip()
            if lab == "START OF ANTENNA":
                cur = [ln]
            elif cur is not None:
                cur.append(ln)
                if lab == "END OF ANTENNA":
                    yield cur
                    cur = None


def _header(path: Path) -> list[str]:
    out = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="ignore") as f:
        for ln in f:
            out.append(ln)
            if ln[60:].strip() == "END OF HEADER":
                break
    return out


def receiver_antennas(files: Iterable[Path]) -> list[dict]:
    """Antenas de receptor disponíveis: [{name, source, l1_up_mm, l2_up_mm}]."""
    seen = {}
    for f in files:
        for b in _blocks(Path(f)):
            name = b[1][:20].rstrip()
            serial = b[1][20:40].strip()
            if serial[:1] in "GRECJ" and serial[1:3].isdigit():
                continue            # satélite
            key = " ".join(name.split())
            if key in seen:
                continue
            ups = {}
            freq = None
            for ln in b:
                lab = ln[60:].strip()
                if lab == "START OF FREQUENCY":
                    freq = ln[3:6].strip()
                elif lab == "NORTH / EAST / UP" and freq:
                    try:
                        ups[freq] = float(ln[20:30])
                    except ValueError:
                        pass
            seen[key] = {"name": key, "source": Path(f).name,
                         "l1_up_mm": ups.get("G01"), "l2_up_mm": ups.get("G02")}
    return sorted(seen.values(), key=lambda a: (not a["name"].startswith("HIT"), a["name"]))


def merged_atx(user_files: Iterable[Path], out: Path, include_ngs: bool = True) -> Path:
    """Um único ANTEX para o RTKLIB: arquivos do usuário primeiro (têm prioridade),
    depois NGS e por fim o igs20 embutido."""
    sources = [Path(f) for f in user_files]
    if include_ngs:
        n = ngs_atx()
        if n:
            sources.append(n)
    sources.append(builtin_atx())
    key = hashlib.md5("|".join(f"{s}:{s.stat().st_size}" for s in sources).encode()).hexdigest()[:10]
    target = out.with_name(f"{out.stem}_{key}.atx")
    if target.exists():
        return target
    with open(target, "w") as fo:
        fo.writelines(_header(builtin_atx()))
        for s in sources:
            for b in _blocks(s):
                fo.writelines(b)
    return target


def antenna_options(user_files: Iterable[Path] = ()) -> list[dict]:
    files = list(user_files)
    n = ngs_atx(download=False)
    if n:
        files.append(n)
    files.append(builtin_atx())
    return receiver_antennas(files)
