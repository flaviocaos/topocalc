from datetime import date, datetime

from topocalc.gnss import antenna, crs, orbits


def test_product_names_and_dirs():
    orbs, clks = orbits._names(date(2026, 10, 8), "GFZ0MGXRAP", ["05M"], ["30S"])
    assert orbs == ["GFZ0MGXRAP_20262810000_01D_05M_ORB.SP3.gz"]
    assert clks == ["GFZ0MGXRAP_20262810000_01D_30S_CLK.CLK.gz"]
    assert orbits.gps_week(date(2026, 10, 8)) == 2439
    dirs = orbits._dirs(date(2026, 10, 8), "GFZ0MGXRAP")
    assert any(d.endswith("/mgex/2439") for d in dirs)


def test_days_for_sessions_and_files_for():
    s = [(datetime(2026, 10, 8, 6, 0), datetime(2026, 10, 8, 15, 0)),
         (datetime(2026, 10, 9, 23, 30), datetime(2026, 10, 10, 2, 0))]
    days = orbits.days_for_sessions(s)
    assert [d.isoformat() for d in days] == ["2026-10-08", "2026-10-09", "2026-10-10"]
    auto = {"2026-10-08": {"ok": True, "files": ["a.SP3", "a.CLK"], "product": "X"}}
    assert orbits.files_for(auto, *s[0]) == ["a.SP3", "a.CLK"]
    assert orbits.files_for(auto, *s[1]) == []     # falta um dia -> transmitidas


def test_ltp_preserves_distances():
    import math
    pts = [{"name": "A", "lat": -8.4364575, "lon": 16.6682933, "h": 656.409},
           {"name": "B", "lat": -8.4377481, "lon": 16.6694084, "h": 655.598}]
    info = crs.add_extra(pts, ["ltp"], 33, True, "A")
    assert "ltp" in info and pts[0]["extra"]["ltp"][:2] == (150000.0, 250000.0)
    e, n, _ = pts[1]["extra"]["ltp"]
    assert abs(math.hypot(e - 150000, n - 250000) - 188.31) < 0.05


def test_builtin_antex_has_satellites_and_hitarget(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPOCALC_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TOPOCALC_OFFLINE", "1")
    names = [a["name"] for a in antenna.antenna_options()]
    assert "HITAT45101CP HITZ" in names
    txt = antenna.builtin_atx().read_text()
    assert txt.count("START OF ANTENNA") > 100 and "G01" in txt
