"""
baseline.py
===========
Processamento de uma linha de base estática com o rnx2rtkp (RTKLIB demo5) e
validação automática da solução.

Estratégia (a mesma validada no levantamento HCB contra o Leica Infinity):

* Linhas curtas (< LIMITE_CURTA km): L1+L2, ionosfera do modelo de navegação,
  troposfera Saastamoinen, resolução de ambiguidades contínua. Processa para
  frente e para trás; a solução fixa é aceita quando as duas concordam.

* Linhas longas: a ionosfera diferencial já não se cancela; estima-se o atraso
  ionosférico por satélite (est-stec) e a troposfera zenital (est-ztd).
  Calcula-se primeiro a solução FLUTUANTE (sem fixar ambiguidades) e depois
  tenta-se FIXAR (fix-and-hold) para frente e para trás. A solução fixa só é
  aceita se:
     - ida e volta estiverem fixas (Q=1) com ratio >= 3 no fim da sessão;
     - ida e volta concordarem (horizontal <= 1,5 cm, vertical <= 3 cm);
     - a média fixa concordar com a flutuante (horizontal <= 2,5 cm,
       vertical <= 5 cm).
  Caso contrário fica a solução flutuante, com precisão a priori maior.

  Em linhas longas, o RTKLIB às vezes declara "fixo" um conjunto de
  ambiguidades errado; o teste ida/volta + flutuante elimina esses casos.

O desvio-padrão atribuído a cada vetor segue um modelo a priori
(a + b·ppm) por tipo de solução, porque o sigma formal do RTKLIB é otimista.
O teste F do ajustamento em rede verifica se esse modelo é adequado.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

from . import rtklib
from .geodesy import ecef_to_llh, enu_matrix

SHORT_KM = 5.0
TOL_FWD_BWD = (0.015, 0.030)    # m, horizontal / vertical
TOL_FIX_FLOAT = (0.025, 0.050)

# precisão a priori (m, ppm) por tipo de solução — horizontal e vertical
APRIORI = {
    "fixa": ((0.003, 0.5), (0.006, 1.0)),
    "flutuante": ((0.010, 1.0), (0.020, 2.0)),
}

COMMON = {
    "pos1-posmode": "static",
    "pos1-elmask": "15",
    "pos1-snrmask_r": "off",
    "pos1-snrmask_b": "off",
    "pos1-dynamics": "off",
    "pos1-tidecorr": "off",
    "pos1-navsys": "9",           # GPS + Galileo (GLONASS tem viés entre fabricantes)
    "pos2-gloarmode": "off",
    "pos2-bdsarmode": "off",
    "pos2-arthres": "3",
    "pos2-arlockcnt": "10",
    "pos2-arelmask": "15",
    "pos2-minfixsats": "4",
    "pos2-elmaskhold": "15",
    "pos2-slipthres": "0.05",
    "pos2-rejionno": "5",
    "pos2-niter": "1",
    "out-solformat": "xyz",
    "out-outhead": "on",
    "out-timesys": "gpst",
    "out-solstatic": "all",
    "stats-eratio1": "300",
    "stats-eratio2": "300",
    "stats-errphase": "0.003",
    "stats-errphaseel": "0.003",
    "stats-prnbias": "0.0001",
    "stats-prniono": "0.001",
    "stats-prntrop": "0.0001",
    "ant1-postype": "llh",
    "ant2-postype": "llh",
    "misc-timeinterp": "off",
}


@dataclass
class BaselineResult:
    id: str
    day: str
    base: str
    rover: str
    length_m: float = 0.0
    duration_min: float = 0.0
    solution: str = "falhou"          # fixa | flutuante | falhou
    method: str = ""
    dxyz: list = field(default_factory=list)      # rover - base (ECEF, marco a marco)
    cov: list = field(default_factory=list)       # 3x3 ECEF (m²)
    denu: list = field(default_factory=list)
    ratio: float = 0.0
    fix_pct: float = 0.0
    fwd_bwd_mm: list = field(default_factory=list)
    fix_float_mm: list = field(default_factory=list)
    sigma_h_m: float = 0.0
    sigma_v_m: float = 0.0
    notes: list = field(default_factory=list)
    excluded: bool = False
    orbits: str = ""
    antennas: list = field(default_factory=list)   # [base, rover]

    def as_dict(self) -> dict:
        return asdict(self)


def _cov_enu_to_ecef(lat, lon, sh, sv) -> np.ndarray:
    R = enu_matrix(lat, lon)
    C = np.diag([sh ** 2, sh ** 2, sv ** 2])
    return R.T @ C @ R


def _last_solution(rows):
    """Última época (solução estática acumulada) -> (xyz, Q, ratio, fix%)."""
    if not rows:
        return None
    last = rows[-1]
    xyz = np.array([float(v) for v in last[2:5]])
    q = int(last[5])
    ratio = float(last[14]) if len(last) > 14 else 0.0
    tail = rows[len(rows) * 2 // 3:]
    fix_tail = sum(1 for r in tail if r[5] == "1") / max(len(tail), 1)
    return {"xyz": xyz, "Q": q, "ratio": ratio, "fix_tail": fix_tail, "n": len(rows)}


def process_baseline(bid: str, day: str, base: dict, rover: dict, navs: list[Path],
                     workdir: Path, base_mark_xyz, interval: int = 10,
                     precise: Optional[list[Path]] = None, atx: Optional[Path] = None,
                     orbit_label: str = "") -> BaselineResult:
    """base/rover: dict com 'name', 'obs' (Path), 'antenna_height', 'first', 'last',
    'approx_xyz' (marco). base_mark_xyz: coordenada do marco da base usada no
    processamento (controle ou já calculada)."""
    res = BaselineResult(id=bid, day=day, base=base["name"], rover=rover["name"])
    workdir.mkdir(parents=True, exist_ok=True)
    bxyz = np.asarray(base_mark_xyz, float)
    lat, lon, h = ecef_to_llh(*bxyz)
    approx_vec = np.asarray(rover["approx_xyz"]) - np.asarray(base["approx_xyz"])
    L = float(np.linalg.norm(approx_vec))
    res.length_m = L
    t0 = max(base["first"], rover["first"])
    t1 = min(base["last"], rover["last"])
    res.duration_min = max((t1 - t0).total_seconds() / 60.0, 0.0)

    cfg = dict(COMMON)
    cfg["ant1-antdelu"] = "%.4f" % rover["antenna_height"]
    cfg["ant2-antdelu"] = "%.4f" % base["antenna_height"]
    files = list(navs)
    if precise:
        cfg["pos1-sateph"] = "precise"
        # em linha de base o relógio do satélite se cancela: basta a órbita (SP3),
        # o que economiza memória e tempo (os arquivos CLK de 30 s são grandes)
        sp3 = [f for f in precise if Path(f).suffix.lower() in (".sp3", ".eph")]
        files += sp3 or list(precise)
        res.orbits = orbit_label or "precisas"
        # órbitas precisas referem-se ao centro de massa do satélite: aplica o
        # deslocamento até a antena do satélite (igs20 embutido)
        try:
            from .antenna import builtin_atx
            cfg["file-satantfile"] = str(builtin_atx())
        except OSError:
            pass
    else:
        cfg["pos1-sateph"] = "brdc"
        res.orbits = "transmitidas"
    # calibração das antenas (ANTEX); sem tipo = sem correção de centro de fase
    if atx and (rover.get("antenna_type") or base.get("antenna_type")):
        cfg["file-rcvantfile"] = str(atx)
        if rover.get("antenna_type"):
            cfg["ant1-anttype"] = rover["antenna_type"]
        if base.get("antenna_type"):
            cfg["ant2-anttype"] = base["antenna_type"]
    res.antennas = [base.get("antenna_type") or "", rover.get("antenna_type") or ""]
    extra = ["-ti", str(interval)]
    R = enu_matrix(lat, lon)

    def run(tag: str, over: dict, soltype: str):
        c = dict(cfg)
        c.update(over)
        c["pos1-soltype"] = soltype
        conf = workdir / f"{bid}_{tag}_{soltype}.conf"
        out = workdir / f"{bid}_{tag}_{soltype}.pos"
        rtklib.write_conf(conf, c)
        rows = rtklib.run_rnx2rtkp(conf, rover["obs"], base["obs"], files, out, (lat, lon, h), extra)
        return _last_solution(rows)

    short = L < SHORT_KM * 1000
    if short:
        res.method = "L1+L2, ionosfera do modelo, ambiguidades contínuas (ida e volta)"
        over = {"pos1-frequency": "l1+l2", "pos1-ionoopt": "brdc", "pos1-tropopt": "saas",
                "pos2-armode": "continuous"}
        fw = run("s", over, "forward")
        bw = run("s", over, "backward")
        flo = None
    else:
        res.method = ("L1+L2(+L5), ionosfera e troposfera estimadas; flutuante + tentativa "
                      "de fixação (fix-and-hold) com validação ida/volta")
        base_over = {"pos1-frequency": "l1+l2", "pos1-ionoopt": "est-stec", "pos1-tropopt": "est-ztd"}
        flo = run("f", {**base_over, "pos2-armode": "off"}, "forward")
        fix_over = {**base_over, "pos1-frequency": "l1+l2+l5", "pos2-armode": "fix-and-hold"}
        fw = run("x", fix_over, "forward")
        bw = run("x", fix_over, "backward")

    sols = [s for s in (fw, bw) if s]
    if not sols and not flo:
        res.notes.append("RTKLIB não produziu solução (sem épocas em comum?).")
        return res

    def enu(s):
        return R @ (s["xyz"] - bxyz)

    accepted = None
    if fw and bw and fw["Q"] == 1 and bw["Q"] == 1 and min(fw["ratio"], bw["ratio"]) >= 3 \
            and min(fw["fix_tail"], bw["fix_tail"]) >= 0.9:
        d = enu(fw) - enu(bw)
        res.fwd_bwd_mm = [round(float(v) * 1000, 1) for v in d]
        ok = np.hypot(d[0], d[1]) <= TOL_FWD_BWD[0] and abs(d[2]) <= TOL_FWD_BWD[1]
        if ok and flo:
            mean = (enu(fw) + enu(bw)) / 2
            df = mean - enu(flo)
            res.fix_float_mm = [round(float(v) * 1000, 1) for v in df]
            ok = np.hypot(df[0], df[1]) <= TOL_FIX_FLOAT[0] and abs(df[2]) <= TOL_FIX_FLOAT[1]
            if not ok:
                res.notes.append("Solução fixa rejeitada: difere da solução flutuante além da tolerância "
                                 "(provável ambiguidade fixada errada).")
        elif not ok:
            res.notes.append("Solução fixa rejeitada: ida e volta discordam.")
        if ok:
            accepted = ("fixa", (fw["xyz"] + bw["xyz"]) / 2)
            res.ratio = round(min(fw["ratio"], bw["ratio"]), 1)
            res.fix_pct = round(100 * min(fw["fix_tail"], bw["fix_tail"]), 0)
    else:
        res.notes.append("Ambiguidades não fixadas de forma consistente nos dois sentidos.")

    if accepted is None:
        if flo:
            accepted = ("flutuante", flo["xyz"])
        else:
            # linha curta sem fixação: média das soluções disponíveis
            accepted = ("flutuante", np.mean([s["xyz"] for s in sols], axis=0))
            if fw and bw:
                d = enu(fw) - enu(bw)
                res.fwd_bwd_mm = [round(float(v) * 1000, 1) for v in d]
        if fw:
            res.ratio = round(fw["ratio"], 1)
            res.fix_pct = round(100 * fw["fix_tail"], 0)

    kind, rxyz = accepted
    res.solution = kind
    vec = rxyz - bxyz
    res.dxyz = [float(v) for v in vec]
    res.denu = [float(v) for v in R @ vec]
    res.length_m = float(np.linalg.norm(vec))
    (ah, bh), (av, bv) = APRIORI[kind]
    sh = ah + bh * 1e-6 * res.length_m
    sv = av + bv * 1e-6 * res.length_m
    # discrepância ida/volta entra na incerteza da flutuante
    if kind == "flutuante" and res.fwd_bwd_mm:
        d = np.array(res.fwd_bwd_mm) / 1000
        sh = max(sh, float(np.hypot(d[0], d[1])) / 2)
        sv = max(sv, abs(float(d[2])) / 2)
    res.sigma_h_m = sh
    res.sigma_v_m = sv
    res.cov = _cov_enu_to_ecef(lat, lon, sh, sv).tolist()
    if res.duration_min < 30:
        res.notes.append("Sessão comum menor que 30 min.")
    return res
