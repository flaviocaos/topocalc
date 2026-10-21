"""
orbits.py
=========
Download automático de órbitas (SP3) e relógios (CLK) precisos dos centros de
análise do IGS, por dia de rastreio.

Ordem de preferência (o primeiro que existir é usado):
  1. produtos FINAIS multi-GNSS (CODE, GFZ, Wuhan) — disponíveis ~2 semanas depois
  2. produtos RÁPIDOS multi-GNSS (GFZ, Wuhan, CODE) — disponíveis ~1 dia depois
  3. produtos FINAIS / RÁPIDOS só GPS do IGS
Os produtos multi-GNSS (MGEX) são preferidos porque o processamento usa
GPS + Galileo; um produto só GPS deixa os satélites Galileo de fora.

Servidores (todos de acesso aberto, HTTPS): IGN e ENSG (França), BKG e GFZ
(Alemanha). O CDDIS (NASA) também é tentado quando há credenciais
Earthdata em TOPOCALC_EARTHDATA_USER / TOPOCALC_EARTHDATA_PASSWORD.

Os arquivos ficam em cache (TOPOCALC_CACHE_DIR, padrão <tmp>/topocalc_cache)
para não baixar de novo o mesmo dia.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

GPS_EPOCH = date(1980, 1, 6)
TIMEOUT = int(os.environ.get("TOPOCALC_DOWNLOAD_TIMEOUT", "60"))
USER_AGENT = "TopoCalc/2 (GNSS post-processing; +https://github.com/flaviocaos/topocalc)"

# (código do produto, amostragem da órbita, amostragens do relógio, descrição)
PRODUCTS = [
    ("COD0MGXFIN", ["05M"], ["30S", "05M"], "CODE final multi-GNSS"),
    ("GFZ0MGXFIN", ["05M", "15M"], ["30S", "05M"], "GFZ final multi-GNSS"),
    ("WUM0MGXFIN", ["05M", "15M"], ["30S", "05M"], "Wuhan final multi-GNSS"),
    ("GRG0MGXFIN", ["05M", "15M"], ["30S", "05M"], "CNES final multi-GNSS"),
    ("GFZ0MGXRAP", ["05M", "15M"], ["30S", "05M"], "GFZ rápido multi-GNSS"),
    ("WUM0MGXRAP", ["05M", "15M"], ["30S", "05M"], "Wuhan rápido multi-GNSS"),
    ("SHA0MGXRAP", ["05M", "15M"], ["05M", "30S"], "SHAO rápido multi-GNSS"),
    ("COD0OPSRAP", ["05M"], ["30S", "05M"], "CODE rápido"),
    ("COD0OPSFIN", ["05M"], ["30S", "05M"], "CODE final"),
    ("IGS0OPSFIN", ["15M"], ["30S", "05M"], "IGS final (combinado)"),
    ("IGS0OPSRAP", ["15M"], ["05M"], "IGS rápido (combinado)"),
]


def gps_week(d: date) -> int:
    return (d - GPS_EPOCH).days // 7


def _dirs(d: date, product: str) -> list[str]:
    """URLs de diretório candidatos para o produto no dia d."""
    w = gps_week(d)
    mgex = "MGX" in product
    out = []
    # IGN e BKG: produtos IGS por semana GPS; MGEX também em /mgex/
    for root in ("https://igs.ign.fr/pub/igs/products", "https://igs.ensg.eu/pub/igs/products",
                 "https://igs.bkg.bund.de/root_ftp/IGS/products"):
        if mgex:
            out.append(f"{root}/mgex/{w}")
        out.append(f"{root}/{w}")
    # mesmos arquivos por FTP anônimo (IGN/ENSG nem sempre respondem por HTTPS)
    for root in ("ftp://igs.ign.fr/pub/igs/products", "ftp://igs.ensg.eu/pub/igs/products"):
        if mgex:
            out.append(f"{root}/mgex/{w}")
        out.append(f"{root}/{w}")
    # CODE (Universidade de Berna): servidor próprio, HTTP aberto, por ano
    if product.startswith("COD"):
        if mgex:
            out.append(f"http://ftp.aiub.unibe.ch/CODE_MGEX/CODE/{d.year}")
        else:
            out.append(f"http://ftp.aiub.unibe.ch/CODE/{d.year}")
            out.append("http://ftp.aiub.unibe.ch/CODE")
    if product.startswith("GFZ"):
        kind = "rapid" if product.endswith("RAP") else "final"
        out.append(f"https://isdc-data.gfz.de/gnss/products/{kind}/w{w}")
        out.append(f"ftp://isdc-ftp.gfz.de/gnss/products/{kind}/w{w}")
    if os.environ.get("TOPOCALC_EARTHDATA_USER"):
        if mgex:
            out.append(f"https://cddis.nasa.gov/archive/gnss/products/mgex/{w}")
        out.append(f"https://cddis.nasa.gov/archive/gnss/products/{w}")
    return out


def _names(d: date, product: str, orb_smp: list[str], clk_smp: list[str]):
    ymd = f"{d.year}{d.timetuple().tm_yday:03d}0000"
    orbs = [f"{product}_{ymd}_01D_{s}_ORB.SP3.gz" for s in orb_smp]
    clks = [f"{product}_{ymd}_01D_{s}_CLK.CLK.gz" for s in clk_smp]
    return orbs, clks


# ------------------------------------------------------------ download

def _opener():
    handlers = []
    user = os.environ.get("TOPOCALC_EARTHDATA_USER")
    pwd = os.environ.get("TOPOCALC_EARTHDATA_PASSWORD")
    if user and pwd:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, "https://urs.earthdata.nasa.gov", user, pwd)
        handlers.append(urllib.request.HTTPBasicAuthHandler(mgr))
        handlers.append(urllib.request.HTTPCookieProcessor())
    return urllib.request.build_opener(*handlers)


OK, MISSING, DEAD = "ok", "missing", "dead"
_LOG: list[str] = []      # últimas tentativas (para o diagnóstico)


def _download(url: str, dest: Path) -> str:
    """Baixa url em dest. Retorna OK, MISSING (servidor respondeu, arquivo não
    existe) ou DEAD (servidor inacessível — não adianta tentar outros arquivos nele)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with _opener().open(req, timeout=TIMEOUT) as r, open(tmp, "wb") as f:
            ctype = r.headers.get("Content-Type", "")
            if "text/html" in ctype:      # página de login/erro em vez do arquivo
                tmp.unlink(missing_ok=True)
                return MISSING
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        _LOG.append(f"{e.code} {url}")
        return MISSING if e.code in (401, 403, 404, 410) else DEAD
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        tmp.unlink(missing_ok=True)
        msg = str(getattr(e, "reason", e))
        _LOG.append(f"erro {msg[:80]} {url}")
        # FTP: "550 arquivo inexistente" significa servidor vivo
        if url.startswith("ftp://") and ("550" in msg or "No such file" in msg):
            return MISSING
        return DEAD
    if tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True)
        return MISSING
    os.replace(tmp, dest)
    _LOG.append(f"ok {url}")
    return OK


def decompress(path: Path) -> Path:
    """Descompacta .gz ou .Z e devolve o arquivo descompactado."""
    name = path.name
    if name.endswith(".gz"):
        out = path.with_name(name[:-3])
        with gzip.open(path, "rb") as fi, open(out, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        return out
    if name.endswith(".Z"):
        out = path.with_name(name[:-2])
        with open(out, "wb") as fo:
            subprocess.run(["gzip", "-dc", str(path)], stdout=fo, check=True)
        return out
    return path


def _valid_sp3(p: Path) -> bool:
    try:
        with open(p, "r", errors="ignore") as f:
            head = f.read(200)
        return head.startswith("#") and ("P" in head[:3] or "V" in head[:3] or head[1] in "cd")
    except OSError:
        return False


def _valid_clk(p: Path) -> bool:
    try:
        with open(p, "r", errors="ignore") as f:
            head = f.read(4000)
        return "RINEX VERSION / TYPE" in head and "END OF HEADER" in head
    except OSError:
        return False


@dataclass
class DayProducts:
    day: str
    product: str = ""
    description: str = ""
    files: list = field(default_factory=list)   # caminhos descompactados (SP3 + CLK)
    source: str = ""
    tried: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.files)


def cache_dir() -> Path:
    d = Path(os.environ.get("TOPOCALC_CACHE_DIR") or Path(tempfile.gettempdir()) / "topocalc_cache") / "orbits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(base: str, names: list[str], cdir: Path, valid, dead: set, res) -> Optional[Path]:
    host = base.split("/")[2]
    for n in names:
        if host in dead:
            return None
        res.tried += 1
        gz = cdir / n
        st = _download(f"{base}/{n}", gz)
        del _LOG[:-60]
        if st == DEAD:
            dead.add(host)
            return None
        if st != OK:
            continue
        try:
            un = decompress(gz)
        except (OSError, EOFError, subprocess.CalledProcessError):
            continue
        finally:
            gz.unlink(missing_ok=True)
        if valid(un):
            return un
        un.unlink(missing_ok=True)
    return None


def fetch_day(d: date, dead: Optional[set] = None, products: Optional[list] = None) -> DayProducts:
    """Procura e baixa SP3 + CLK do dia d. Nunca levanta exceção de rede."""
    res = DayProducts(day=d.isoformat())
    cdir = cache_dir()
    dead = dead if dead is not None else set()
    plist = list(products or PRODUCTS)
    if (date.today() - d).days < 12:
        # finais ainda não saíram: tenta primeiro os rápidos (economiza dezenas de tentativas)
        plist.sort(key=lambda x: 0 if x[0].endswith("RAP") else 1)
    for product, orb_smp, clk_smp, desc in plist:
        orbs, clks = _names(d, product, orb_smp, clk_smp)
        cached_orb = next((cdir / o[:-3] for o in orbs if _valid_sp3(cdir / o[:-3])), None)
        cached_clk = next((cdir / c[:-3] for c in clks if _valid_clk(cdir / c[:-3])), None)
        if cached_orb and cached_clk:
            res.product, res.description, res.source = product, desc, "cache"
            res.files = [str(cached_orb), str(cached_clk)]
            return res
        for base in _dirs(d, product):
            orb = cached_orb or _get(base, orbs, cdir, _valid_sp3, dead, res)
            if not orb:
                continue
            clk = cached_clk or _get(base, clks, cdir, _valid_clk, dead, res)
            if not clk:
                cached_orb = orb      # guarda a órbita e tenta o relógio em outro servidor
                continue
            res.product, res.description = product, desc
            res.source = base.split("/")[2]
            res.files = [str(orb), str(clk)]
            return res
    res.message = ("Órbitas precisas não encontradas para este dia (ainda não publicadas ou "
                   "servidores inacessíveis). Serão usadas as efemérides transmitidas.")
    if dead:
        res.message += " Servidores sem resposta: " + ", ".join(sorted(dead)) + "."
    return res


def days_for_sessions(intervals: Iterable[tuple[datetime, datetime]], margin_h: float = 1.0) -> list[date]:
    """Dias UTC necessários para cobrir as sessões (com margem para a interpolação)."""
    days = set()
    m = timedelta(hours=margin_h)
    for t0, t1 in intervals:
        d = (t0 - m).date()
        while d <= (t1 + m).date():
            days.add(d)
            d += timedelta(days=1)
    return sorted(days)


def fetch_for_project(intervals: Iterable[tuple[datetime, datetime]]) -> dict[str, dict]:
    out = {}
    dead: set = set()
    for d in days_for_sessions(intervals):
        t = time.time()
        r = fetch_day(d, dead)
        out[d.isoformat()] = {"ok": r.ok, "product": r.product, "description": r.description,
                              "files": r.files, "source": r.source, "message": r.message,
                              "seconds": round(time.time() - t, 1)}
    return out


def files_for(precise_auto: dict, t0: datetime, t1: datetime) -> list[str]:
    """Arquivos de órbita/relógio que cobrem o intervalo [t0, t1]."""
    files: list[str] = []
    for d in days_for_sessions([(t0, t1)]):
        info = precise_auto.get(d.isoformat())
        if not info or not info.get("ok"):
            return []            # falta um dia: melhor usar só as transmitidas
        files += info["files"]
    return files
