"""
project.py
==========
Orquestra um projeto de pós-processamento: do upload dos .GNS até as
coordenadas finais ajustadas. O estado fica em <pasta>/project.json, de modo
que a interface web e o CLI podem retomar qualquer etapa.

Etapas
------
1. analyze()   lê cabeçalhos, converte para RINEX, junta navegações do dia,
               calcula posição aproximada (SPP) e propõe nomes, alturas e
               linhas de base.
2. (revisão)   o usuário confere nomes dos marcos, alturas de antena,
               coordenadas de controle e quais linhas processar.
3. process()   processa as linhas de base a partir do controle (em ondas:
               cada linha usa como base um ponto já conhecido), ajusta a rede
               e gera os entregáveis.
4. readjust()  exclui/inclui vetores e reajusta sem reprocessar.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import antenna, crs, orbits, rtklib
from .adjust import Control, adjust_network
from .baseline import process_baseline
from .geodesy import ecef_to_llh, egm96, enu_matrix, from_utm, llh_to_ecef, to_utm, utm_zone
from .gns_file import read_header, guess_point_name
from .rinex_tools import convert_gns, merge_nav

SAME_MARK_M = 10.0
DEFAULT_SETTINGS = {
    "min_overlap_min": 30,
    "interval_s": 10,
    "utm_zone": None,          # automático
    "hemisphere": None,        # automático
    "workers": None,           # automático
    "orbits": "auto",          # auto (precisas quando houver) | broadcast
    "frame": "WGS84",          # referencial das coordenadas do controle (rótulo de saída)
    "extra_crs": [],           # camacupa2015 | camacupa1948 | ltp
    "ltp_origin": None,        # ponto de origem do plano topográfico local
}

_locks: dict[str, threading.Lock] = {}


# ------------------------------------------------------------ persistência

def _pfile(pdir: Path) -> Path:
    return pdir / "project.json"


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))


def load(pdir: Path) -> dict:
    return json.loads(_pfile(pdir).read_text())


def save(pdir: Path, p: dict) -> None:
    tmp = _pfile(pdir).with_suffix(".tmp")
    tmp.write_text(json.dumps(p, default=_default, ensure_ascii=False, indent=1))
    os.replace(tmp, _pfile(pdir))


def _dt(s) -> datetime:
    return s if isinstance(s, datetime) else datetime.fromisoformat(s)


class Progress:
    """Atualiza status/progresso no project.json (lido pela página de acompanhamento)."""

    def __init__(self, pdir: Path, stage: str):
        self.pdir = pdir
        self.stage = stage
        self.lock = threading.Lock()

    def __call__(self, pct: Optional[float] = None, msg: Optional[str] = None):
        with self.lock:
            p = load(self.pdir)
            p["status"] = "running"
            p["stage"] = self.stage
            if pct is not None:
                p["progress"] = round(float(pct), 1)
            if msg:
                p.setdefault("log", []).append(f"{datetime.now():%H:%M:%S}  {msg}")
                p["message"] = msg
            save(self.pdir, p)


# ------------------------------------------------------------ etapa 1

def create(pdir: Path, gns_files: list[Path], precise_files: Optional[list[Path]] = None,
           title: str = "", antex_files: Optional[list[Path]] = None) -> dict:
    pdir.mkdir(parents=True, exist_ok=True)
    p = {
        "id": pdir.name, "title": title or "Levantamento GNSS", "created": datetime.now(),
        "status": "created", "stage": "upload", "progress": 0, "log": [], "message": "",
        "settings": dict(DEFAULT_SETTINGS),
        "files": [{"path": str(f), "filename": f.name} for f in gns_files],
        "precise": [str(f) for f in (precise_files or [])],
        "antex_user": [str(f) for f in (antex_files or [])],
        "occupations": [], "controls": [], "baselines": [], "suggestions": [],
        "results": None,
    }
    save(pdir, p)
    return p


def analyze(pdir: Path) -> None:
    prog = Progress(pdir, "analyze")
    try:
        _analyze(pdir, prog)
    except Exception as e:  # noqa: BLE001
        _fail(pdir, e)


def _fail(pdir: Path, e: Exception) -> None:
    p = load(pdir)
    p["status"] = "error"
    p["message"] = f"{type(e).__name__}: {e}"
    p.setdefault("log", []).append(traceback.format_exc()[-2000:])
    save(pdir, p)


def _analyze(pdir: Path, prog: Progress) -> None:
    p = load(pdir)
    rinex_dir = pdir / "rinex"
    occs = []
    n = len(p["files"])
    prog(1, f"Lendo {n} arquivo(s) .GNS")
    for k, f in enumerate(p["files"]):
        path = Path(f["path"])
        h = read_header(path)
        if not h.signature_ok:
            prog(None, f"AVISO: {path.name} não parece ser um .GNS Hi-Target — ignorado.")
            continue
        name = guess_point_name(path, h.survey_date)
        stem = path.stem.upper()
        prog(5 + 60 * k / max(n, 1), f"Convertendo {path.name} para RINEX ({h.data_format})")
        try:
            info = convert_gns(h, rinex_dir, stem, name, h.antenna_height_m or 0.0)
        except Exception as e:  # noqa: BLE001
            prog(None, f"ERRO ao converter {path.name}: {e}")
            continue
        occs.append({
            "id": stem, "file": path.name, "path": str(path), "name": name,
            "antenna_height": h.antenna_height_m or 0.0,
            "antenna_height_header": h.antenna_height_m,
            "receiver": h.model_name or h.model_code, "serial": h.serial,
            "model_code": h.model_code,
            "format": h.data_format, "interval_s": info.get("interval_s") or h.interval_s,
            "first": info["first"], "last": info["last"], "n_epochs": info["n_epochs"],
            "day": info["first"].date().isoformat() if info["first"] else
                   (h.survey_date.isoformat() if h.survey_date else ""),
            "obs": str(info["obs"]), "proc_obs": str(info["proc_obs"]), "nav": str(info["nav"]),
            "header_warnings": h.warnings + info.get("warnings", []),
            "include": True,
        })

    if not occs:
        raise ValueError("Nenhum arquivo pôde ser convertido.")

    days = sorted({o["day"] for o in occs})
    navs = {}
    for d in days:
        navs[d] = str(merge_nav([Path(o["nav"]) for o in occs if o["day"] == d],
                                rinex_dir / f"nav_{d}.nav"))
    prog(70, "Calculando posições aproximadas (SPP)")
    (pdir / "work").mkdir(exist_ok=True)
    for k, o in enumerate(occs):
        s = rtklib.spp(Path(o["proc_obs"]), [Path(navs[o["day"]])], pdir / "work" / f"spp_{o['id']}.pos")
        if s:
            arp = np.array(s["xyz"])
            R = enu_matrix(s["lat"], s["lon"])
            mark = arp - R[2] * o["antenna_height"]
            o["approx_xyz"] = mark.tolist()
            o["approx_llh"] = list(ecef_to_llh(*mark))
            o["spp_sigma_m"] = s["sigma_m"]
        else:
            o["approx_xyz"] = None
            o["header_warnings"].append("Posição aproximada (SPP) falhou: poucas épocas válidas.")
        prog(70 + 25 * (k + 1) / len(occs), None)

    p = load(pdir)
    # arquivos de órbita enviados pelo usuário (podem vir compactados)
    up = []
    for f in p.get("precise", []):
        try:
            up.append(str(orbits.decompress(Path(f))))
        except Exception:  # noqa: BLE001
            prog(None, f"AVISO: não consegui ler {Path(f).name}.")
    p["precise"] = up
    if not up and p["settings"].get("orbits", "auto") == "auto":
        prog(96, "Procurando órbitas e relógios precisos dos dias do rastreio")
        p["precise_auto"] = orbits.fetch_for_project([(o["first"], o["last"]) for o in occs])
        for d, info in p["precise_auto"].items():
            prog(None, f"Órbitas {d}: " + (f"{info['description']} ({info['source']})" if info["ok"]
                                            else "não disponíveis — efemérides transmitidas"))
    prog(98, "Carregando calibrações de antena")
    try:
        antenna.ngs_atx()            # baixa uma vez (cache); sem rede, segue sem ela
        p["antenna_options"] = antenna.antenna_options([Path(f) for f in p.get("antex_user", [])])
        # sugestão de antena pelo código do modelo gravado no .GNS (ex.: HITV90, HITV200)
        names = {a["name"] for a in p["antenna_options"]}
        for o in occs:
            code = (o.get("model_code") or "").strip().upper()
            o["antenna_suggested"] = next((n for n in (f"{code} NONE", code) if n in names), "")
    except Exception as e:  # noqa: BLE001
        p["antenna_options"] = []
        prog(None, f"AVISO: calibrações de antena indisponíveis ({e}).")
    p["occupations"] = occs
    p["navs"] = navs
    p["suggestions"] = _suggest(occs)
    p["baselines"] = propose_baselines(occs, p["settings"]["min_overlap_min"])
    p["status"] = "review"
    p["stage"] = "review"
    p["progress"] = 100
    p["message"] = f"{len(occs)} arquivo(s) convertidos em {len(days)} dia(s). Revise os dados."
    p["log"].append(f"{datetime.now():%H:%M:%S}  {p['message']}")
    save(pdir, p)


def _suggest(occs: list[dict]) -> list[str]:
    out = []
    for i, a in enumerate(occs):
        for b in occs[i + 1:]:
            if not a.get("approx_xyz") or not b.get("approx_xyz"):
                continue
            d = float(np.linalg.norm(np.subtract(a["approx_xyz"], b["approx_xyz"])))
            if d < SAME_MARK_M and a["name"] != b["name"]:
                if a["day"] == b["day"]:
                    out.append(f"{a['file']} e {b['file']} estão a {d:.1f} m um do outro no mesmo dia "
                               f"— confira se são marcos diferentes.")
                else:
                    out.append(f"{a['file']} ({a['name']}, {a['day']}) e {b['file']} ({b['name']}, {b['day']}) "
                               f"estão a {d:.1f} m: provavelmente o mesmo marco. Use o mesmo nome.")
            elif d > 50 and a["name"] == b["name"]:
                out.append(f"{a['file']} e {b['file']} têm o mesmo nome ({a['name']}) mas estão a {d:.0f} m.")
    for o in occs:
        if not o.get("antenna_height_header"):
            out.append(f"{o['file']}: altura da antena não encontrada no arquivo — informe.")
    return out


def propose_baselines(occs: list[dict], min_overlap_min: float) -> list[dict]:
    out = []
    for i, a in enumerate(occs):
        for b in occs[i + 1:]:
            if a["day"] != b["day"]:
                continue
            t0 = max(_dt(a["first"]), _dt(b["first"]))
            t1 = min(_dt(a["last"]), _dt(b["last"]))
            ov = (t1 - t0).total_seconds() / 60
            if ov <= 0:
                continue
            L = None
            if a.get("approx_xyz") and b.get("approx_xyz"):
                L = float(np.linalg.norm(np.subtract(a["approx_xyz"], b["approx_xyz"])))
            out.append({"id": f"{a['id']}-{b['id']}", "occ_a": a["id"], "occ_b": b["id"],
                        "day": a["day"], "overlap_min": round(ov, 1),
                        "approx_length_m": L, "include": ov >= min_overlap_min,
                        "result": None})
    return out


# ------------------------------------------------------------ controle

def parse_control(c: dict) -> dict:
    """Normaliza um marco de controle digitado pelo usuário.

    Aceita: {'name', 'system': 'utm'|'geo', 'E','N','zone','hemisphere' ou
    'lat','lon', 'h' (elipsoidal) e/ou 'H' (ortométrica), 'sigma_h','sigma_v'}.
    """
    name = str(c["name"]).strip().upper()
    if c.get("system") == "ppp":
        return {"name": name, "ppp": True, "lat": None, "lon": None, "h": None, "H": None,
                "xyz": None, "sigma_h": 0.0, "sigma_v": 0.0, "notes": [], "input": c,
                "H_input": float(c["H"]) if c.get("H") not in (None, "") else None}
    if c.get("system", "utm") == "utm":
        zone = int(c["zone"])
        south = str(c.get("hemisphere", "S")).upper().startswith("S")
        lat, lon = from_utm(float(c["E"]), float(c["N"]), zone, south)
    else:
        lat, lon = _parse_angle(c["lat"]), _parse_angle(c["lon"])
    h = c.get("h")
    H = c.get("H")
    h = float(h) if h not in (None, "") else None
    H = float(H) if H not in (None, "") else None
    notes = []
    if h is None and H is None:
        raise ValueError(f"Controle {name}: informe a altitude elipsoidal (h) ou ortométrica (H).")
    if h is None:
        h = H + egm96(lat, lon)
        notes.append("h calculada de H + EGM96 (sem altitude elipsoidal informada)")
    return {"name": name, "lat": lat, "lon": lon, "h": h, "H": H,
            "xyz": llh_to_ecef(lat, lon, h).tolist(),
            "sigma_h": float(c.get("sigma_h") or 0), "sigma_v": float(c.get("sigma_v") or 0),
            "notes": notes, "input": c}


def _parse_angle(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().upper().replace(",", ".")
    neg = s.startswith("-") or s.endswith("S") or s.endswith("W") or s.endswith("O")
    for ch in "°º'\"NSEWO:":
        s = s.replace(ch, " ")
    parts = [float(x) for x in s.replace("-", " ").split()]
    val = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
    return -val if neg else val


# ------------------------------------------------------------ etapa 3

def process(pdir: Path) -> None:
    prog = Progress(pdir, "process")
    try:
        _process(pdir, prog)
    except Exception as e:  # noqa: BLE001
        _fail(pdir, e)


def _process(pdir: Path, prog: Progress) -> None:
    p = load(pdir)
    occ = {o["id"]: o for o in p["occupations"] if o.get("include", True)}
    controls = {c["name"]: c for c in p["controls"]}
    if not controls:
        raise ValueError("Informe ao menos um marco de controle com coordenadas conhecidas.")
    names = {o["name"] for o in occ.values()}
    missing = [c for c in controls if c not in names]
    if len(missing) == len(controls):
        raise ValueError("Nenhum marco de controle corresponde a um nome de ponto rastreado "
                         f"({', '.join(sorted(controls))}).")

    bls = [b for b in p["baselines"] if b.get("include") and b["occ_a"] in occ and b["occ_b"] in occ
           and occ[b["occ_a"]]["name"] != occ[b["occ_b"]]["name"]]
    if not bls:
        raise ValueError("Nenhuma linha de base selecionada.")

    work = pdir / "work"
    work.mkdir(exist_ok=True)
    interval = int(p["settings"].get("interval_s") or 10)

    # órbitas precisas: arquivos do usuário têm prioridade; senão, busca automática
    user_precise = [Path(x) for x in p.get("precise", [])]
    auto = p.get("precise_auto") or {}
    if not user_precise and p["settings"].get("orbits", "auto") == "auto":
        missing_days = orbits.days_for_sessions([(_dt(o["first"]), _dt(o["last"])) for o in occ.values()])
        if any(not (auto.get(d.isoformat()) or {}).get("ok") for d in missing_days):
            prog(1, "Procurando órbitas precisas")
            auto.update({k: v for k, v in orbits.fetch_for_project(
                [(_dt(o["first"]), _dt(o["last"])) for o in occ.values()]).items()
                if v["ok"] or k not in auto})
            p["precise_auto"] = auto
            save(pdir, p)

    def precise_for(t0, t1):
        if user_precise:
            return user_precise, "precisas (arquivos enviados)"
        if p["settings"].get("orbits", "auto") != "auto":
            return None, "transmitidas"
        files = orbits.files_for(auto, t0, t1)
        if not files:
            return None, "transmitidas"
        prods = sorted({auto[d.isoformat()]["product"] for d in orbits.days_for_sessions([(t0, t1)])})
        return [Path(f) for f in files], "precisas " + "/".join(prods)

    # calibração de antenas (o igs20 embutido também traz os satélites, usado com órbitas precisas)
    antenna.builtin_atx()
    use_ant = any(o.get("antenna_type") for o in occ.values())
    ppp_ctrls = [c for c in controls.values() if c.get("ppp")]
    atx = None
    if use_ant or ppp_ctrls:
        atx = antenna.merged_atx([Path(f) for f in p.get("antex_user", [])], work / "antenas.atx")

    # controle por PPP
    for c in ppp_ctrls:
        sols = []
        for o in [o for o in occ.values() if o["name"] == c["name"]]:
            t0, t1 = _dt(o["first"]), _dt(o["last"])
            files, label = precise_for(t0, t1)
            if not files:
                raise ValueError(f"PPP de {c['name']}: não há órbitas e relógios precisos para "
                                 f"{t0:%d/%m/%Y}. Envie os arquivos SP3/CLK ou informe as coordenadas.")
            prog(1, f"PPP de {c['name']} ({o['file']}, {(t1 - t0).total_seconds() / 3600:.1f} h)")
            r = rtklib.ppp_static(Path(o["proc_obs"]), [Path(p["navs"][o["day"]])], files, atx,
                                  work / f"ppp_{o['id']}.pos", float(o["antenna_height"]),
                                  o.get("antenna_type") or "", interval=30)
            if r:
                r["hours"] = (t1 - t0).total_seconds() / 3600
                r["file"] = o["file"]
                r["orbits"] = label
                sols.append(r)
        if not sols:
            raise ValueError(f"PPP de {c['name']} não convergiu.")
        w = np.array([s_["hours"] for s_ in sols])
        xyz = (np.array([s_["xyz"] for s_ in sols]) * w[:, None]).sum(0) / w.sum()
        lat, lon, h = ecef_to_llh(*xyz)
        hours = float(w.sum())
        # precisão realista de PPP flutuante (o sigma formal do RTKLIB é otimista)
        sh = 0.02 if hours >= 6 else 0.03 if hours >= 3 else 0.06
        sv = 2 * sh
        c.update({"xyz": xyz.tolist(), "lat": lat, "lon": lon, "h": h, "sigma_h": sh, "sigma_v": sv,
                  "H": c.get("H_input"), "ppp_solutions": sols,
                  "notes": [f"Coordenada por PPP estático ({hours:.1f} h, {sols[0]['orbits']}); "
                            f"precisão adotada σH {sh * 100:.0f} cm / σV {sv * 100:.0f} cm"]})
        prog(None, f"PPP de {c['name']}: h = {h:.3f} m ({hours:.1f} h de dados)")
    if ppp_ctrls:
        p["controls"] = list(controls.values())
        save(pdir, p)

    known: dict[str, np.ndarray] = {n: np.array(c["xyz"]) for n, c in controls.items()}
    done: dict[str, dict] = {}
    pending = list(bls)
    total = len(bls)
    workers = (p["settings"].get("workers") or int(os.environ.get("TOPOCALC_WORKERS", "0") or 0)
               or max(1, min(2, os.cpu_count() or 1)))
    prog(2, f"Processando {total} linha(s) de base a partir de {', '.join(sorted(known))} "
            f"({workers} em paralelo)")

    def job(b):
        a, c = occ[b["occ_a"]], occ[b["occ_b"]]
        if a["name"] in known and (c["name"] not in known or _rank(a["name"], controls) <= _rank(c["name"], controls)):
            base, rover = a, c
        else:
            base, rover = c, a
        bdict = _station(base)
        rdict = _station(rover)
        t0 = max(bdict["first"], rdict["first"])
        t1 = min(bdict["last"], rdict["last"])
        files, label = precise_for(t0, t1)
        r = process_baseline(b["id"], b["day"], bdict, rdict, [Path(p["navs"][b["day"]])],
                             work, known[base["name"]], interval=interval, precise=files,
                             atx=atx, orbit_label=label)
        return b, r

    while pending:
        wave = [b for b in pending if occ[b["occ_a"]]["name"] in known or occ[b["occ_b"]]["name"] in known]
        if not wave:
            break
        pending = [b for b in pending if b not in wave]
        with ThreadPoolExecutor(workers) as ex:
            for b, r in ex.map(job, wave):
                done[b["id"]] = r.as_dict()
                prog(2 + 88 * len(done) / total,
                     f"{r.base} → {r.rover}: {r.solution}, {r.length_m / 1000:.2f} km"
                     + (f", ratio {r.ratio}" if r.ratio else ""))
        # novos pontos conhecidos: base + vetor (prefere solução fixa, depois mais curta)
        cand: dict[str, list] = {}
        for b in wave:
            r = done[b["id"]]
            if r["solution"] == "falhou":
                continue
            if r["rover"] not in known:
                cand.setdefault(r["rover"], []).append(r)
        for name, rs in cand.items():
            rs.sort(key=lambda r: (r["solution"] != "fixa", r["length_m"]))
            r = rs[0]
            known[name] = known[r["base"]] + np.array(r["dxyz"])

    p = load(pdir)
    for b in p["baselines"]:
        if b["id"] in done:
            b["result"] = done[b["id"]]
    unreached = sorted({occ[b["occ_a"]]["name"] for b in pending} | {occ[b["occ_b"]]["name"] for b in pending})
    p["unreached"] = unreached
    p["known_approx"] = {k: v.tolist() for k, v in known.items()}
    save(pdir, p)
    if unreached:
        prog(None, "AVISO: pontos sem ligação com o controle: " + ", ".join(unreached))
    prog(92, "Ajustando a rede")
    _adjust_and_report(pdir, prog)


def _rank(name: str, controls: dict) -> int:
    return 0 if name in controls else 1


def _station(o: dict) -> dict:
    return {"name": o["name"], "obs": Path(o["proc_obs"]), "antenna_height": float(o["antenna_height"]),
            "antenna_type": o.get("antenna_type") or "",
            "first": _dt(o["first"]), "last": _dt(o["last"]),
            "approx_xyz": o.get("approx_xyz") or [0, 0, 0]}


def readjust(pdir: Path) -> None:
    prog = Progress(pdir, "adjust")
    try:
        _adjust_and_report(pdir, prog)
    except Exception as e:  # noqa: BLE001
        _fail(pdir, e)


def _adjust_and_report(pdir: Path, prog: Progress) -> None:
    p = load(pdir)
    vectors = []
    for b in p["baselines"]:
        r = b.get("result")
        if not r or r["solution"] == "falhou":
            continue
        v = dict(r)
        v["excluded"] = bool(b.get("excluded"))
        vectors.append(v)
    controls = [Control(c["name"], np.array(c["xyz"]), c.get("sigma_h", 0), c.get("sigma_v", 0))
                for c in p["controls"]]
    approx = {k: np.array(v) for k, v in p.get("known_approx", {}).items()}
    adj = adjust_network(vectors, controls, approx)
    if not adj.ok:
        raise ValueError(adj.message)

    points = _final_points(p, adj)
    extra_info = {}
    if points:
        st = p["settings"]
        extra_info = crs.add_extra(points, st.get("extra_crs") or [], points[0]["zone"],
                                   points[0]["hemisphere"] == "S", st.get("ltp_origin"))
    p = load(pdir)
    p["results"] = {
        "computed": datetime.now(),
        "adjustment": {
            "dof": adj.dof, "sigma0_sq": adj.sigma0_sq, "chi2": adj.chi2,
            "f_crit": adj.f_crit, "global_test_passed": adj.global_test_passed,
            "message": adj.message, "unconnected": adj.unconnected,
            "n_vectors": len([v for v in vectors if not v["excluded"]]),
            "n_excluded": len([v for v in vectors if v["excluded"]]),
        },
        "observations": adj.observations,
        "loops": adj.loops,
        "points": points,
        "geoid": _geoid_info(p),
        "frame": crs.frame_label(p["settings"].get("frame", "WGS84")),
        "extra_crs": extra_info,
        "orbits": _orbit_summary(p),
        "antennas": sorted({o.get("antenna_type") or "" for o in p["occupations"]} - {""}),
    }
    save(pdir, p)
    prog(95, "Gerando entregáveis (relatório, planilha, KML, CSV, RINEX)")
    from ..reports import build_all
    files = build_all(pdir)
    p = load(pdir)
    p["outputs"] = [str(f) for f in files]
    p["status"] = "done"
    p["stage"] = "results"
    p["progress"] = 100
    a = p["results"]["adjustment"]
    p["message"] = ("Concluído. Teste global " + ("aprovado" if a["global_test_passed"] else "REPROVADO")
                    + f" (σ0² = {a['sigma0_sq']:.2f}, crítico {a['f_crit']:.2f}).")
    p["log"].append(f"{datetime.now():%H:%M:%S}  {p['message']}")
    save(pdir, p)


def _orbit_summary(p: dict) -> dict:
    """Órbitas usadas por dia (para relatório/tela)."""
    used = {}
    for b in p["baselines"]:
        r = b.get("result")
        if r:
            used.setdefault(b["day"], set()).add(r.get("orbits") or "transmitidas")
    return {d: ", ".join(sorted(v)) for d, v in sorted(used.items())}


def _geoid_info(p: dict) -> dict:
    offs = []
    for c in p["controls"]:
        if c.get("H") is not None and c.get("h") is not None:
            n_obs = c["h"] - c["H"]
            offs.append({"name": c["name"], "N_obs": n_obs, "N_egm96": egm96(c["lat"], c["lon"]),
                         "diff": n_obs - egm96(c["lat"], c["lon"])})
    off = float(np.mean([o["diff"] for o in offs])) if offs else 0.0
    return {"model": "EGM96" + (" ajustado ao controle" if offs else ""), "offset": off, "controls": offs}


def _final_points(p: dict, adj) -> list[dict]:
    geo = _geoid_info(p)
    s = p["settings"]
    lons = [pt["lon"] for pt in adj.points.values()]
    lats = [pt["lat"] for pt in adj.points.values()]
    zone = s.get("utm_zone") or utm_zone(float(np.mean(lons)))
    south = (s.get("hemisphere") or ("S" if np.mean(lats) < 0 else "N")).upper().startswith("S")
    nvec: dict[str, dict] = {}
    for b in p["baselines"]:
        r = b.get("result")
        if not r or b.get("excluded") or r["solution"] == "falhou":
            continue
        for n in (r["base"], r["rover"]):
            d = nvec.setdefault(n, {"fixa": 0, "flutuante": 0})
            d[r["solution"]] += 1
    occ_by_name: dict[str, list] = {}
    for o in p["occupations"]:
        occ_by_name.setdefault(o["name"], []).append(o)
    out = []
    ctrl = {c["name"]: c for c in p["controls"]}
    for name, pt in sorted(adj.points.items(), key=lambda kv: (not kv[1]["control"], _natkey(kv[0]))):
        E, N, z, hemi, conv, k = to_utm(pt["lat"], pt["lon"], zone, south)
        Ng = egm96(pt["lat"], pt["lon"]) + geo["offset"]
        H = pt["h"] - Ng
        if name in ctrl and ctrl[name].get("H") is not None:
            H = ctrl[name]["H"]
        out.append({
            "name": name, "lat": pt["lat"], "lon": pt["lon"], "h": pt["h"],
            "E": E, "N": N, "zone": z, "hemisphere": hemi, "convergence": conv, "scale": k,
            "N_geoid": Ng, "H": H, "sE": pt["sE"], "sN": pt["sN"], "sU": pt["sU"],
            "type": "controle" if pt["control"] else "ajustado",
            "fixed": pt["fixed"], "vectors": nvec.get(name, {"fixa": 0, "flutuante": 0}),
            "occupations": [{"file": o["file"], "day": o["day"], "antenna_height": o["antenna_height"],
                             "first": o["first"], "last": o["last"], "receiver": o["receiver"]}
                            for o in occ_by_name.get(name, [])],
        })
    return out


def _natkey(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


# ------------------------------------------------------------ execução em segundo plano

def run_background(pdir: Path, fn: Callable[[Path], None]) -> threading.Thread:
    p = load(pdir)
    p["status"] = "running"
    p["progress"] = 0
    p["message"] = "Iniciando…"
    save(pdir, p)
    t = threading.Thread(target=fn, args=(pdir,), daemon=True, name=f"topocalc-{pdir.name}")
    t.start()
    return t
