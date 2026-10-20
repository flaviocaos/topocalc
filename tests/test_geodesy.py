
from topocalc.gnss.geodesy import ecef_to_llh, egm96, from_utm, llh_to_ecef, to_utm


def test_ecef_roundtrip():
    for lat, lon, h in [(-8.4365, 16.6683, 656.4), (45.0, -120.0, 10.0), (-89.0, 179.0, 3000.0)]:
        la, lo, hh = ecef_to_llh(*llh_to_ecef(lat, lon, h))
        assert abs(la - lat) < 1e-10 and abs(lo - lon) < 1e-10 and abs(hh - h) < 1e-4


def test_utm_roundtrip_and_egm96():
    lat, lon = from_utm(683668.738, 9067058.188, 33, True)
    E, N, z, hemi, _, _ = to_utm(lat, lon, 33, True)
    assert abs(E - 683668.738) < 1e-4 and abs(N - 9067058.188) < 1e-4 and hemi == "S"
    # EGM96 no marco HBD01 (Angola) — conferido com o egm96-universal
    assert abs(egm96(lat, lon) - 8.185) < 0.005
