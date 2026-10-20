import numpy as np

from topocalc.gnss.adjust import Control, adjust_network, chi2_ppf
from topocalc.gnss.geodesy import enu_matrix, llh_to_ecef


def _net(blunder=None, seed=1):
    rng = np.random.default_rng(seed)
    true = {"A": llh_to_ecef(-8.4, 16.6, 650), "B": llh_to_ecef(-8.45, 16.62, 700),
            "C": llh_to_ecef(-8.5, 16.58, 800), "D": llh_to_ecef(-8.42, 16.7, 600)}
    R = enu_matrix(-8.45, 16.6)
    cov = R.T @ np.diag([0.005 ** 2, 0.005 ** 2, 0.01 ** 2]) @ R
    pairs = [("A", "B"), ("A", "C"), ("B", "C"), ("B", "D"), ("C", "D"), ("A", "D")]
    vec = []
    for i, (a, b) in enumerate(pairs):
        d = true[b] - true[a] + rng.multivariate_normal(np.zeros(3), cov)
        if blunder == i:
            d = d + R[2] * 0.15
        vec.append({"id": f"{a}-{b}", "base": a, "rover": b, "dxyz": d.tolist(), "cov": cov.tolist()})
    approx = {k: v + 3.0 for k, v in true.items()}
    return true, vec, approx


def test_recovers_coordinates():
    true, vec, approx = _net()
    r = adjust_network(vec, [Control("A", true["A"])], approx)
    assert r.ok and r.dof == 18 - 9
    for k in "BCD":
        assert np.linalg.norm(np.array(r.points[k]["xyz"]) - true[k]) < 0.03
    assert r.global_test_passed
    assert len(r.loops) == 4


def test_detects_blunder():
    true, vec, approx = _net(blunder=2)
    r = adjust_network(vec, [Control("A", true["A"])], approx)
    worst = max(r.observations, key=lambda o: o["w_max"])
    assert worst["id"] == "B-C" and worst["flag"] == "rejeitar"


def test_unconnected_point_reported():
    true, vec, approx = _net()
    vec.append({"id": "X-Y", "base": "X", "rover": "Y", "dxyz": [1, 2, 3], "cov": np.eye(3).tolist()})
    approx["X"] = approx["Y"] = np.zeros(3)
    r = adjust_network(vec, [Control("A", true["A"])], approx)
    assert r.ok and r.unconnected == ["X", "Y"]


def test_chi2_quantile():
    assert abs(chi2_ppf(0.95, 10) - 18.307) < 0.05
