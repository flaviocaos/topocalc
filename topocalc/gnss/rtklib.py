"""
rtklib.py
=========
Chamadas aos programas de linha de comando do RTKLIB (versão demo5 de
rtklibexplorer): ``convbin`` (binário -> RINEX) e ``rnx2rtkp``
(posicionamento por ponto e linhas de base).

Os executáveis são procurados, nesta ordem:
  1. na pasta indicada pela variável de ambiente TOPOCALC_RTKLIB_DIR;
  2. em topocalc/bin/ (onde o Dockerfile os instala);
  3. no PATH do sistema.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from .geodesy import ecef_to_llh, llh_to_ecef

PKG_BIN = Path(__file__).resolve().parent.parent / "bin"


class RtklibNotFound(RuntimeError):
    pass


def find_binary(name: str) -> str:
    cands = []
    env = os.environ.get("TOPOCALC_RTKLIB_DIR")
    if env:
        cands.append(Path(env) / name)
    cands.append(PKG_BIN / name)
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    w = shutil.which(name)
    if w:
        return w
    raise RtklibNotFound(
        f"Executável '{name}' do RTKLIB não encontrado. Instale o RTKLIB demo5 "
        "(o Dockerfile já faz isso) ou defina TOPOCALC_RTKLIB_DIR.")


def available() -> bool:
    try:
        find_binary("convbin")
        find_binary("rnx2rtkp")
        return True
    except RtklibNotFound:
        return False


def run_convbin(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([find_binary("convbin")] + args, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout)


def _read_pos(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [ln.split() for ln in path.read_text(errors="ignore").splitlines()
            if ln.strip() and not ln.startswith("%")]


def spp(obs: Path, navs: Iterable[Path], out: Path, interval: int = 30) -> Optional[dict]:
    """Posição por ponto (código) — mediana robusta das épocas. Retorna o ARP."""
    conf = out.with_suffix(".conf")
    write_conf(conf, {"pos1-posmode": "single", "pos1-frequency": "l1+l2",
                      "pos1-ionoopt": "dual-freq", "pos1-tropopt": "saas",
                      "pos1-elmask": "15", "pos1-navsys": "13", "out-solformat": "xyz"})
    args = [find_binary("rnx2rtkp"), "-k", str(conf),
            "-ti", str(interval), "-o", str(out), str(obs)] + [str(n) for n in navs]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    rows = _read_pos(out)
    if len(rows) < 5:
        return None
    a = np.array([[float(v) for v in r[2:5]] for r in rows])
    med = np.median(a, axis=0)
    dist = np.linalg.norm(a - med, axis=1)
    keep = a[dist < max(3 * np.median(dist), 5.0)]
    xyz = keep.mean(axis=0)
    lat, lon, h = ecef_to_llh(*xyz)
    return {"xyz": xyz.tolist(), "lat": lat, "lon": lon, "h": h, "n": int(len(keep)),
            "sigma_m": float(np.std(np.linalg.norm(keep - xyz, axis=1)))}


def write_conf(path: Path, cfg: dict) -> None:
    path.write_text("".join("%-20s=%s\n" % kv for kv in cfg.items()))


def run_rnx2rtkp(conf: Path, rover: Path, base: Path, navs: Iterable[Path], out: Path,
                 base_llh: tuple[float, float, float], extra: Optional[list[str]] = None,
                 timeout: int = 3600) -> list[list[str]]:
    args = [find_binary("rnx2rtkp"), "-k", str(conf),
            "-l", "%.10f" % base_llh[0], "%.10f" % base_llh[1], "%.4f" % base_llh[2],
            "-o", str(out)]
    if extra:
        args += extra
    args += [str(rover), str(base)] + [str(n) for n in navs]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    return _read_pos(out)


__all__ = ["find_binary", "available", "run_convbin", "spp", "write_conf", "run_rnx2rtkp",
           "RtklibNotFound", "ecef_to_llh", "llh_to_ecef"]
