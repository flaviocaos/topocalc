"""Teste ponta a ponta com arquivos .GNS reais.

Defina TOPOCALC_TEST_DATA com a pasta dos .GNS do levantamento HCB (Angola,
out/2026). Sem ela (ou sem o RTKLIB), o teste é ignorado.
"""
import os
from pathlib import Path

import pytest

from topocalc.gnss import project as P
from topocalc.gnss import rtklib

DATA = os.environ.get("TOPOCALC_TEST_DATA")

# Coordenadas finais entregues (Leica Infinity, órbitas precisas)
REF = {"HCB1": (683790.935, 9066914.931, 655.585), "HCB4": (682668.695, 9056047.601, 670.748),
       "HCB10": (669220.973, 9035353.306, 953.384), "HCB14": (679820.495, 9021378.575, 895.422)}


@pytest.mark.skipif(not DATA or not rtklib.available(), reason="sem dados reais ou RTKLIB")
def test_hcb_network(tmp_path):
    files = sorted(Path(DATA).glob("*.GNS"))
    P.create(tmp_path, files, title="HCB")
    P.analyze(tmp_path)
    p = P.load(tmp_path)
    assert p["status"] == "review" and len(p["occupations"]) == 12
    ren = {"HBD1": "HBD01", "HC10": "HCB10", "HC12": "HCB12", "HC13": "HCB13", "HC14": "HCB14", "HCB02": "HCB2"}
    for o in p["occupations"]:
        o["name"] = ren.get(o["name"], o["name"])
    p["controls"] = [P.parse_control(dict(name="HBD01", system="utm", E=683668.738, N=9067058.188,
                                          zone=33, hemisphere="S", h=656.4086, H=648.3203))]
    P.save(tmp_path, p)
    P.process(tmp_path)
    p = P.load(tmp_path)
    assert p["status"] == "done", p["message"]
    pts = {x["name"]: x for x in p["results"]["points"]}
    for k, (E, N, h) in REF.items():
        assert abs(pts[k]["E"] - E) < 0.05 and abs(pts[k]["N"] - N) < 0.05
        assert abs(pts[k]["h"] - h) < 0.10
