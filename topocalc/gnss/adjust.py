"""
adjust.py
=========
Ajustamento em rede 3D (ECEF) de vetores GNSS pelo método dos mínimos
quadrados (modelo paramétrico), com:

* um ou mais marcos de controle fixos (ou com peso, se tiverem sigma);
* teste global (qui-quadrado / F) da variância a posteriori;
* teste w de Baarda (data snooping) por componente de cada vetor;
* fechamento de triângulos independentes do ajustamento.

Vetores com |w| acima do crítico podem ser excluídos e a rede reajustada.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .geodesy import ecef_to_llh, enu_matrix

W_ATTENTION = 1.96     # alfa = 5 %
W_REJECT = 3.29        # alfa = 0,1 % (Baarda)


def _norm_ppf(p: float) -> float:
    # Acklam — suficiente para quantis de teste
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def chi2_ppf(p: float, k: int) -> float:
    """Quantil do qui-quadrado (Wilson–Hilferty)."""
    if k <= 0:
        return float("nan")
    z = _norm_ppf(p)
    return k * (1 - 2 / (9 * k) + z * math.sqrt(2 / (9 * k))) ** 3


@dataclass
class Control:
    name: str
    xyz: np.ndarray
    sigma_h: float = 0.0   # 0 = fixo
    sigma_v: float = 0.0


@dataclass
class AdjustmentResult:
    ok: bool
    message: str = ""
    dof: int = 0
    sigma0_sq: float = float("nan")
    chi2: float = float("nan")
    chi2_low: float = float("nan")
    chi2_high: float = float("nan")
    f_crit: float = float("nan")
    global_test_passed: bool = False
    points: dict = field(default_factory=dict)       # nome -> dict
    observations: list = field(default_factory=list)  # por vetor
    loops: list = field(default_factory=list)
    unconnected: list = field(default_factory=list)


def connected_components(names: set, edges: list[tuple[str, str]]) -> list[set]:
    adj = {n: set() for n in names}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def adjust_network(vectors: list[dict], controls: list[Control],
                   approx: dict[str, np.ndarray]) -> AdjustmentResult:
    """vectors: dicts com id, base, rover, dxyz (3), cov (3x3), excluded.
    approx: coordenadas aproximadas ECEF de todos os pontos."""
    use = [v for v in vectors if not v.get("excluded") and v.get("dxyz")]
    if not use:
        return AdjustmentResult(False, "Nenhum vetor válido para ajustar.")
    names = set()
    for v in use:
        names.add(v["base"])
        names.add(v["rover"])
    ctrl = {c.name: c for c in controls if c.name in names}
    if not ctrl:
        return AdjustmentResult(False, "Nenhum marco de controle está ligado aos vetores processados.")

    comps = connected_components(names, [(v["base"], v["rover"]) for v in use])
    unconnected = sorted(n for comp in comps if not (comp & set(ctrl)) for n in comp)
    if unconnected:
        bad = set(unconnected)
        use = [v for v in use if v["base"] not in bad and v["rover"] not in bad]
        names -= bad

    fixed = {n for n, c in ctrl.items() if c.sigma_h <= 0 and c.sigma_v <= 0}
    free = sorted(names - fixed)
    idx = {n: i for i, n in enumerate(free)}
    nu = 3 * len(free)

    rows_A, rows_l, blocks_P = [], [], []
    X0 = {n: (ctrl[n].xyz if n in ctrl else np.asarray(approx[n], float)) for n in names}

    for v in use:
        A = np.zeros((3, nu))
        if v["rover"] in idx:
            A[:, 3 * idx[v["rover"]]:3 * idx[v["rover"]] + 3] = np.eye(3)
        if v["base"] in idx:
            A[:, 3 * idx[v["base"]]:3 * idx[v["base"]] + 3] -= np.eye(3)
        l = np.asarray(v["dxyz"]) - (X0[v["rover"]] - X0[v["base"]])
        C = np.asarray(v["cov"], float)
        rows_A.append(A)
        rows_l.append(l)
        blocks_P.append(np.linalg.inv(C))
    # controles com peso: pseudo-observação de posição
    for n, c in ctrl.items():
        if n in fixed or n not in idx:
            continue
        A = np.zeros((3, nu))
        A[:, 3 * idx[n]:3 * idx[n] + 3] = np.eye(3)
        lat, lon, _ = ecef_to_llh(*c.xyz)
        R = enu_matrix(lat, lon)
        C = R.T @ np.diag([c.sigma_h ** 2, c.sigma_h ** 2, max(c.sigma_v, 1e-4) ** 2]) @ R
        rows_A.append(A)
        rows_l.append(c.xyz - X0[n])
        blocks_P.append(np.linalg.inv(C))

    A = np.vstack(rows_A)
    l = np.concatenate(rows_l)
    nobs = len(l)
    P = np.zeros((nobs, nobs))
    for k, Pk in enumerate(blocks_P):
        P[3 * k:3 * k + 3, 3 * k:3 * k + 3] = Pk
    N = A.T @ P @ A
    if nu and np.linalg.matrix_rank(N) < nu:
        return AdjustmentResult(False, "Rede com geometria insuficiente (matriz normal singular).")
    Qxx = np.linalg.inv(N) if nu else np.zeros((0, 0))
    x = Qxx @ A.T @ P @ l if nu else np.zeros(0)
    vres = A @ x - l
    dof = nobs - nu
    vtpv = float(vres @ P @ vres)
    s0 = vtpv / dof if dof > 0 else float("nan")

    res = AdjustmentResult(True, dof=dof, sigma0_sq=s0, unconnected=unconnected)
    if dof > 0:
        res.chi2 = vtpv
        res.chi2_low = chi2_ppf(0.025, dof)
        res.chi2_high = chi2_ppf(0.975, dof)
        res.f_crit = chi2_ppf(0.95, dof) / dof
        res.global_test_passed = s0 <= res.f_crit
    else:
        res.global_test_passed = True
        res.message = "Rede sem redundância (cada ponto determinado por um único vetor)."

    # coordenadas ajustadas
    for n in sorted(names):
        if n in idx:
            i = idx[n]
            X = X0[n] + x[3 * i:3 * i + 3]
            Q = Qxx[3 * i:3 * i + 3, 3 * i:3 * i + 3]
        else:
            X = X0[n]
            Q = np.zeros((3, 3))
        lat, lon, h = ecef_to_llh(*X)
        R = enu_matrix(lat, lon)
        # sigma a posteriori só amplia quando o teste global indica subestimação
        scale = max(s0, 1.0) if dof > 0 and not math.isnan(s0) else 1.0
        Qenu = R @ Q @ R.T * scale
        res.points[n] = {
            "xyz": X.tolist(), "lat": lat, "lon": lon, "h": h,
            "sE": math.sqrt(max(Qenu[0, 0], 0)), "sN": math.sqrt(max(Qenu[1, 1], 0)),
            "sU": math.sqrt(max(Qenu[2, 2], 0)),
            "control": n in ctrl, "fixed": n in fixed,
        }

    # resíduos e teste w por vetor
    Qll = np.linalg.inv(P) if nobs else P
    Qvv = Qll - A @ Qxx @ A.T if nu else Qll
    PQvvP = P @ Qvv @ P
    for k, v in enumerate(use):
        sl = slice(3 * k, 3 * k + 3)
        vv = vres[sl]
        bx = X0[v["base"]] + (x[3 * idx[v["base"]]:3 * idx[v["base"]] + 3] if v["base"] in idx else 0)
        lat, lon, _ = ecef_to_llh(*bx)
        R = enu_matrix(lat, lon)
        venu = R @ vv
        # w-test por componente ENU (direções e_E, e_N, e_U no espaço ECEF)
        ws = []
        for comp in range(3):
            c = np.zeros(nobs)
            c[sl] = R[comp]           # direção da componente em ECEF
            # estatística w para o erro grosseiro na direção c (correlacionado)
            num = float(c @ P @ vres)
            den = float(c @ PQvvP @ c)
            ws.append(num / math.sqrt(den) if den > 1e-20 else 0.0)
        redund = float(np.trace((Qvv @ P)[sl, sl]))
        res.observations.append({
            "id": v["id"], "base": v["base"], "rover": v["rover"],
            "v_enu_mm": [float(t) * 1000 for t in venu],
            "w": [float(t) for t in ws], "w_max": float(max(abs(t) for t in ws)),
            "redundancy": redund,
            "flag": ("rejeitar" if max(abs(t) for t in ws) > W_REJECT else
                     "atenção" if max(abs(t) for t in ws) > W_ATTENTION else "ok") if dof > 0 else "sem redundância",
        })
    res.loops = triangle_closures(use, {n: np.asarray(p["xyz"]) for n, p in res.points.items()})
    return res


def triangle_closures(vectors: list[dict], positions: Optional[dict] = None,
                      max_loops: int = 80) -> list[dict]:
    """Fechamento de triângulos formados por vetores (independe de controle)."""
    by_pair: dict[tuple, list] = {}
    for v in vectors:
        by_pair.setdefault(tuple(sorted((v["base"], v["rover"]))), []).append(v)
    pts = sorted({p for pair in by_pair for p in pair})
    out = []

    def vec(a, b):
        cand = by_pair.get(tuple(sorted((a, b))))
        if not cand:
            return None
        v = cand[0]
        d = np.asarray(v["dxyz"])
        return (d if v["base"] == a else -d), v

    for a, b, c in itertools.combinations(pts, 3):
        ab, bc, ca = vec(a, b), vec(b, c), vec(c, a)
        if not (ab and bc and ca):
            continue
        mis = ab[0] + bc[0] + ca[0]
        per = float(np.linalg.norm(ab[0]) + np.linalg.norm(bc[0]) + np.linalg.norm(ca[0]))
        item = {"points": [a, b, c], "misclosure_mm": float(np.linalg.norm(mis) * 1000),
                "perimeter_m": per,
                "ppm": float(np.linalg.norm(mis) / per * 1e6) if per else 0.0,
                "days": sorted({x[1].get("day", "") for x in (ab, bc, ca)} - {""}),
                "vectors": [ab[1]["id"], bc[1]["id"], ca[1]["id"]]}
        if positions and a in positions:
            lat, lon, _ = ecef_to_llh(*positions[a])
            e = enu_matrix(lat, lon) @ mis
            item["mis_enu_mm"] = [float(t) * 1000 for t in e]
        out.append(item)
        if len(out) >= max_loops:
            break
    return out
