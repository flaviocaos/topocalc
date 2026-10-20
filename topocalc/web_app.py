"""
web_app.py
==========
Interface web do TopoCalc (Flask).

Fluxo: enviar .GNS -> conversão/análise em segundo plano -> revisão (nomes,
alturas, controle, linhas de base) -> processamento em segundo plano ->
resultados (excluir vetores e reajustar) -> download dos entregáveis.

Variáveis de ambiente:
  TOPOCALC_DATA_DIR    pasta dos projetos (padrão: <tmp>/topocalc_projects)
  TOPOCALC_PASSWORD    se definida, exige login (usuário qualquer, esta senha)
  TOPOCALC_KEEP_DAYS   dias para manter projetos antigos (padrão 7)
  TOPOCALC_RTKLIB_DIR  pasta com convbin e rnx2rtkp (opcional)
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.utils import secure_filename

from . import __version__
from .gnss import project as P
from .gnss import rtklib
from .reports.common import dms, fmt_day, fmt_dt, fmt_hm

app = Flask(__name__)
app.secret_key = os.environ.get("TOPOCALC_SECRET_KEY") or secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("TOPOCALC_MAX_UPLOAD_MB", "1500")) * 1024 * 1024

DATA_DIR = Path(os.environ.get("TOPOCALC_DATA_DIR") or Path(tempfile.gettempdir()) / "topocalc_projects")
DATA_DIR.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ.get("TOPOCALC_PASSWORD")
KEEP_DAYS = float(os.environ.get("TOPOCALC_KEEP_DAYS", "7"))

_threads: dict[str, threading.Thread] = {}

app.jinja_env.filters.update(dms=dms, day=fmt_day, dt=fmt_dt, hm=fmt_hm)


# ------------------------------------------------------------ autenticação simples

def _auth_ok() -> bool:
    if not PASSWORD:
        return True
    a = request.authorization
    return bool(a and a.password and secrets.compare_digest(a.password, PASSWORD))


@app.before_request
def _require_auth():
    if request.endpoint in ("health", "static"):
        return None
    if not _auth_ok():
        return Response("Acesso restrito.", 401, {"WWW-Authenticate": 'Basic realm="TopoCalc"'})
    return None


# ------------------------------------------------------------ utilidades

def _pdir(pid: str) -> Path:
    if not pid.isalnum() or len(pid) > 40:
        abort(404)
    d = DATA_DIR / pid
    if not (d / "project.json").exists():
        abort(404)
    return d


def _load(pid: str) -> dict:
    d = _pdir(pid)
    p = P.load(d)
    # servidor reiniciado no meio de uma tarefa
    if p.get("status") == "running" and not (_threads.get(pid) and _threads[pid].is_alive()):
        p["status"] = "error"
        p["message"] = "A tarefa foi interrompida (o servidor reiniciou). Execute novamente."
        P.save(d, p)
    return p


def _start(pid: str, fn) -> None:
    t = _threads.get(pid)
    if t and t.is_alive():
        flash("Já existe uma tarefa em andamento neste projeto.")
        return
    _threads[pid] = P.run_background(_pdir(pid), fn)


def _cleanup() -> None:
    limit = time.time() - KEEP_DAYS * 86400
    for d in DATA_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < limit and d.name not in _threads:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _f(v, default=None):
    if v is None:
        return default
    v = str(v).strip().replace(",", ".")
    if v == "":
        return default
    return float(v)


# ------------------------------------------------------------ rotas

@app.route("/health")
def health():
    return jsonify(ok=True, version=__version__, rtklib=rtklib.available())


@app.route("/")
def index():
    _cleanup()
    projects = []
    for d in sorted(DATA_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if (d / "project.json").exists():
            try:
                p = P.load(d)
                projects.append(p)
            except Exception:  # noqa: BLE001
                continue
    return render_template("index.html", projects=projects[:30], rtklib_ok=rtklib.available(),
                           version=__version__, keep_days=KEEP_DAYS)


@app.route("/projetos", methods=["POST"])
def create_project():
    files = [f for f in request.files.getlist("gns") if f and f.filename]
    if not files:
        flash("Selecione os arquivos .GNS.")
        return redirect(url_for("index"))
    pid = uuid.uuid4().hex[:12]
    d = DATA_DIR / pid
    inp = d / "input"
    inp.mkdir(parents=True)
    saved = []
    for f in files:
        name = secure_filename(f.filename) or f"arquivo_{len(saved)}.GNS"
        dest = inp / name
        f.save(dest)
        saved.append(dest)
    precise = []
    for f in request.files.getlist("precise"):
        if f and f.filename:
            dest = inp / secure_filename(f.filename)
            f.save(dest)
            precise.append(dest)
    P.create(d, saved, precise, title=request.form.get("title", "").strip())
    _start(pid, P.analyze)
    return redirect(url_for("project", pid=pid))


@app.route("/p/<pid>")
def project(pid):
    p = _load(pid)
    if p["status"] in ("running", "created"):
        return render_template("progress.html", p=p, version=__version__)
    if p["status"] == "review" or (request.args.get("revisar") and p.get("occupations")):
        occ = {o["id"]: o for o in p["occupations"]}
        p["suggestions"] = P._suggest(p["occupations"])  # reflete os nomes já corrigidos
        return render_template("review.html", p=p, occ=occ, version=__version__)
    if p["status"] == "error" or not p.get("results"):
        return render_template("error.html", p=p, version=__version__)
    return render_template("results.html", p=p, version=__version__,
                           outputs=[Path(f).name for f in p.get("outputs", [])])


@app.route("/p/<pid>/status")
def status(pid):
    p = _load(pid)
    return jsonify(status=p["status"], stage=p.get("stage"), progress=p.get("progress", 0),
                   message=p.get("message", ""), log=p.get("log", [])[-12:])


@app.route("/p/<pid>/revisao", methods=["POST"])
def review(pid):
    d = _pdir(pid)
    p = _load(pid)
    form = request.form
    errors = []
    p["title"] = form.get("title", p.get("title", "")).strip() or p.get("title")
    for o in p["occupations"]:
        k = o["id"]
        o["name"] = (form.get(f"name_{k}") or o["name"]).strip().upper()
        try:
            o["antenna_height"] = _f(form.get(f"ant_{k}"), o["antenna_height"])
        except ValueError:
            errors.append(f"Altura de antena inválida em {o['file']}.")
        o["include"] = form.get(f"inc_{k}") == "on"
    for b in p["baselines"]:
        b["include"] = form.get(f"bl_{b['id']}") == "on"
        b["excluded"] = False
        b["result"] = None

    controls = []
    for i in range(int(form.get("n_ctrl", "0"))):
        name = (form.get(f"c{i}_name") or "").strip()
        if not name:
            continue
        system = form.get(f"c{i}_system", "utm")
        try:
            c = {"name": name, "system": system,
                 "E": _f(form.get(f"c{i}_E")), "N": _f(form.get(f"c{i}_N")),
                 "zone": form.get(f"c{i}_zone") or None, "hemisphere": form.get(f"c{i}_hem") or "S",
                 "lat": (form.get(f"c{i}_lat") or "").strip(), "lon": (form.get(f"c{i}_lon") or "").strip(),
                 "h": _f(form.get(f"c{i}_h")), "H": _f(form.get(f"c{i}_H")),
                 "sigma_h": _f(form.get(f"c{i}_sh"), 0), "sigma_v": _f(form.get(f"c{i}_sv"), 0)}
            if system == "utm" and (c["E"] is None or c["N"] is None or not c["zone"]):
                raise ValueError("informe E, N e fuso")
            if system == "geo" and (not c["lat"] or not c["lon"]):
                raise ValueError("informe latitude e longitude")
            controls.append(P.parse_control(c))
        except (ValueError, TypeError, KeyError) as e:
            errors.append(f"Controle {name}: {e}")
    p["controls"] = controls

    s = p["settings"]
    try:
        s["interval_s"] = int(_f(form.get("interval_s"), s.get("interval_s") or 10))
        z = form.get("utm_zone", "").strip()
        s["utm_zone"] = int(z) if z else None
        s["hemisphere"] = form.get("hemisphere") or None
    except ValueError:
        errors.append("Configurações inválidas.")

    names = {o["name"] for o in p["occupations"] if o["include"]}
    if not controls:
        errors.append("Informe ao menos um marco de controle.")
    elif not any(c["name"] in names for c in controls):
        errors.append("O nome do marco de controle precisa ser igual ao nome de um ponto rastreado "
                      f"(pontos: {', '.join(sorted(names))}).")
    if not any(b["include"] for b in p["baselines"]):
        errors.append("Selecione ao menos uma linha de base.")
    p["results"] = None
    p["outputs"] = []
    P.save(d, p)
    if errors or form.get("action") == "save":
        for e in errors:
            flash(e)
        if not errors:
            flash("Revisão salva.")
        return redirect(url_for("project", pid=pid, revisar=1))
    _start(pid, P.process)
    return redirect(url_for("project", pid=pid))


@app.route("/p/<pid>/reajustar", methods=["POST"])
def readjust(pid):
    d = _pdir(pid)
    p = _load(pid)
    excl = set(request.form.getlist("excluir"))
    for b in p["baselines"]:
        if b.get("result"):
            b["excluded"] = b["id"] in excl
    P.save(d, p)
    _start(pid, P.readjust)
    return redirect(url_for("project", pid=pid))


@app.route("/p/<pid>/arquivo/<path:fname>")
def download(pid, fname):
    d = _pdir(pid) / "output"
    return send_from_directory(d, fname, as_attachment=True)


@app.route("/p/<pid>/excluir", methods=["POST"])
def delete(pid):
    d = _pdir(pid)
    if _threads.get(pid) and _threads[pid].is_alive():
        flash("Aguarde a tarefa terminar para excluir o projeto.")
        return redirect(url_for("project", pid=pid))
    shutil.rmtree(d, ignore_errors=True)
    flash("Projeto excluído.")
    return redirect(url_for("index"))


@app.errorhandler(413)
def too_large(_e):
    flash("Arquivos grandes demais para um envio. Envie em menos arquivos ou aumente TOPOCALC_MAX_UPLOAD_MB.")
    return redirect(url_for("index"))


def run_dev_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1", threaded=True)


if __name__ == "__main__":
    run_dev_server()
