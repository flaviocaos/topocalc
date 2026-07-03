"""
pdf_report.py
=============
Converte o relatório .docx já gerado em .pdf usando LibreOffice headless.
Mantemos o docx como fonte única de verdade do conteúdo do relatório, para
que docx e pdf nunca divirjam.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PdfConversionError(RuntimeError):
    pass


def _find_soffice() -> str:
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    raise PdfConversionError(
        "LibreOffice ('soffice'/'libreoffice') não encontrado no PATH. "
        "Instale o pacote libreoffice para habilitar a exportação em PDF."
    )


def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path:
    soffice = _find_soffice()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        soffice, "--headless", "--norestore",
        "--convert-to", "pdf", "--outdir", str(output_dir), str(docx_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise PdfConversionError(
            f"Falha ao converter docx -> pdf via LibreOffice.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

    pdf_path = output_dir / (docx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise PdfConversionError(f"Conversão concluída mas '{pdf_path}' não foi encontrado.")
    return pdf_path
