"""
web_app.py
==========
Interface web simples (Flask) sobre o mesmo pipeline usado pelo CLI.

Uso:
    python -m gns_processor.web_app
    (abre em http://127.0.0.1:5000)
"""

from __future__ import annotations

import base64
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from flask import Flask, request, render_template_string, send_file, redirect, url_for, flash

from .pipeline import run_pipeline
from .docx_report import build_docx_report
from .pdf_report import convert_docx_to_pdf, PdfConversionError
from .kml_export import export_kml
from .xlsx_export import export_xlsx
from .validate import run_all_validations
from .assets import icon_png_path

app = Flask(__name__)
app.secret_key = "topocalc-dev-key"  # apenas para uso local/dev

JOBS_DIR = Path(tempfile.gettempdir()) / "topocalc_jobs"
JOBS_DIR.mkdir(exist_ok=True)


def _icon_data_uri() -> str:
    icon_path = icon_png_path()
    if icon_path is None:
        return ""
    data = base64.b64encode(icon_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"

PAGE_TEMPLATE = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <title>TopoCalc — Pacote Geodésico</title>
  {% if icon_uri %}<link rel="icon" href="{{ icon_uri }}">{% endif %}
  <style>
    body { font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; color: #3A3A39;
           background: #F5F5F4; }
    .brand { display: flex; align-items: center; gap: 14px; }
    .brand img { width: 46px; height: 46px; }
    .brand h1 { font-size: 1.5rem; font-weight: 600; margin: 0; color: #3A3A39; }
    .brand h1 span { color: #1E8A93; }
    .box { border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-top: 20px;
           background: #ffffff; }
    label { display: block; margin-top: 12px; font-weight: 600; }
    input[type=file], input[type=text] { width: 100%; padding: 6px; margin-top: 4px; }
    button { margin-top: 18px; padding: 10px 18px; background: #1E8A93; color: white; border: none;
             border-radius: 6px; cursor: pointer; font-size: 1rem; }
    button:hover { background: #17707A; }
    .flash { background: #fdecea; color: #611a15; padding: 10px; border-radius: 6px; margin-top: 10px; }
    .ok { background: #e7f6e7; color: #1e4620; }
    ul { padding-left: 18px; }
    .note { color: #6E6F71; font-size: 0.9rem; }
  </style>
</head>
<body>
  <div class="brand">
    {% if icon_uri %}<img src="{{ icon_uri }}" alt="TopoCalc">{% endif %}
    <h1>Topo<span>Calc</span> — Pacote de Entregáveis Geodésicos</h1>
  </div>
  <p class="note">Envie 2 ou mais arquivos .GNS. Se não indicar qual é a BASE, o sistema detecta
  automaticamente pelo maior número de sentenças ZDA.</p>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, message in messages %}
      <div class="flash {{ 'ok' if category=='ok' else '' }}">{{ message }}</div>
    {% endfor %}
  {% endwith %}

  <div class="box">
    <form method="post" action="{{ url_for('process_files') }}" enctype="multipart/form-data">
      <label>Arquivos .GNS</label>
      <input type="file" name="files" multiple required accept=".gns">

      <label>Nome do arquivo BASE (opcional — deixe em branco para detecção automática)</label>
      <input type="text" name="base_name" placeholder="ex: estacao01.gns">

      <button type="submit">Processar</button>
    </form>
  </div>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE_TEMPLATE, icon_uri=_icon_data_uri())


@app.route("/process", methods=["POST"])
def process_files():
    files = request.files.getlist("files")
    base_name = request.form.get("base_name") or None

    if len(files) < 2:
        flash("Envie ao menos 2 arquivos .GNS (1 base + 1 ponto).", "error")
        return redirect(url_for("index"))

    job_id = uuid.uuid4().hex[:10]
    job_dir = JOBS_DIR / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for f in files:
        dest = input_dir / f.filename
        f.save(dest)
        saved_paths.append(dest)

    try:
        result = run_pipeline(saved_paths, forced_base_name=base_name)
        docx_path = build_docx_report(result, output_dir)
        try:
            convert_docx_to_pdf(docx_path, output_dir)
        except PdfConversionError:
            pass  # PDF é best-effort na interface web; docx continua disponível
        export_kml(result, output_dir / "pontos_levantamento.kml")
        xlsx_path = export_xlsx(result, output_dir / "planilha_geodesica.xlsx")
        run_all_validations(result, docx_path, xlsx_path, output_dir)
    except Exception as e:
        flash(f"Erro no processamento: {e}", "error")
        return redirect(url_for("index"))

    zip_path = job_dir / "pacote_geodesico.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.glob("*"):
            if f.is_file():
                zf.write(f, arcname=f.name)

    return send_file(str(zip_path), as_attachment=True, download_name="pacote_geodesico.zip")


def run_dev_server():
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    run_dev_server()
