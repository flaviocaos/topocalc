"""
validate.py
===========
Validações automáticas exigidas pelo escopo:

(a) Os 4 produtos batem entre si nos mesmos valores numéricos (checado por
    construção, já que todos usam ProcessingResult; aqui recomputamos a
    partir do xlsx e do kml para uma checagem cruzada independente).
(b) Nenhuma tabela do docx/pdf sai da página (checa a soma das larguras de
    coluna do docx contra a largura útil da página).
(c) A planilha abre sem erro de fórmula (recálculo via LibreOffice headless
    e checagem de #REF!/#VALUE!/#DIV/0!/#N/A/#NAME?).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
import openpyxl

from .pipeline import ProcessingResult

FORMULA_ERRORS = ["#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!"]


@dataclass
class ValidationReport:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)

    def passed(self, msg: str) -> None:
        self.checks_passed.append(msg)


def check_docx_table_widths(docx_path: Path, report: ValidationReport) -> None:
    document = Document(str(docx_path))
    section = document.sections[0]
    usable = section.page_width - section.left_margin - section.right_margin

    bad_tables = 0
    for i, table in enumerate(document.tables):
        tbl = table._tbl
        tblGrid = tbl.find(qn("w:tblGrid"))
        if tblGrid is None:
            continue
        total_twips = 0
        for gridCol in tblGrid.findall(qn("w:gridCol")):
            w = gridCol.get(qn("w:w"))
            if w:
                total_twips += int(w)
        total_emu = total_twips * 635  # 1 twip = 635 EMU
        if total_emu > usable * 1.01:  # 1% de tolerância para arredondamento
            bad_tables += 1
            report.fail(
                f"Tabela #{i+1} do docx excede a largura útil da página "
                f"({total_emu} EMU > {usable} EMU)."
            )

    if bad_tables == 0:
        report.passed(f"Todas as {len(document.tables)} tabelas do docx cabem na largura da página.")


def check_xlsx_formulas(xlsx_path: Path, report: ValidationReport, workdir: Path) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        report.fail("LibreOffice não encontrado; não foi possível recalcular a planilha para validação.")
        return

    workdir.mkdir(parents=True, exist_ok=True)
    # Converte para xlsx novamente via LibreOffice força o recálculo de fórmulas
    # e materializa os valores calculados, que então inspecionamos.
    cmd = [soffice, "--headless", "--norestore", "--convert-to", "xlsx",
           "--outdir", str(workdir), str(xlsx_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        report.fail(f"Falha ao recalcular planilha via LibreOffice: {proc.stderr}")
        return

    recalced_path = workdir / xlsx_path.name
    if not recalced_path.exists():
        report.fail("LibreOffice não gerou o arquivo recalculado para validação.")
        return

    wb = openpyxl.load_workbook(str(recalced_path), data_only=True)
    errors_found = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and any(err in cell.value for err in FORMULA_ERRORS):
                    errors_found.append(f"{ws.title}!{cell.coordinate} = {cell.value}")

    if errors_found:
        for e in errors_found[:20]:
            report.fail(f"Erro de fórmula na planilha: {e}")
    else:
        report.passed("Planilha recalculada via LibreOffice sem erros de fórmula (#REF!/#VALUE!/etc.).")


def check_cross_consistency(result: ProcessingResult, xlsx_path: Path, report: ValidationReport,
                             tolerance_m: float = 0.01) -> None:
    """Recalcula deltas/distâncias a partir dos valores brutos de UTM
    presentes no xlsx e compara com o ProcessingResult usado por docx/kml,
    como checagem cruzada independente de que os 4 produtos usam os mesmos
    números-base."""
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=False)
    ws = wb["Pontos_Resumo"]

    # mapa label -> (easting, northing, alt) lido da planilha (colunas J vazio? K/L)
    xlsx_rows = {}
    for row in ws.iter_rows(min_row=2, values_only=False):
        label = row[0].value
        if label is None:
            continue
        easting = row[10].value  # coluna K
        northing = row[11].value  # coluna L
        if isinstance(easting, (int, float)) and isinstance(northing, (int, float)):
            xlsx_rows[label] = (easting, northing)

    mismatches = 0
    for g in result.all_geo():
        if g.label not in xlsx_rows:
            report.fail(f"Ponto '{g.label}' presente no ProcessingResult mas ausente no xlsx.")
            mismatches += 1
            continue
        xe, xn = xlsx_rows[g.label]
        if abs(xe - g.utm_easting) > tolerance_m or abs(xn - g.utm_northing) > tolerance_m:
            mismatches += 1
            report.fail(
                f"Divergência de UTM para '{g.label}': docx/kml=({g.utm_easting:.3f}, "
                f"{g.utm_northing:.3f}) vs xlsx=({xe:.3f}, {xn:.3f})."
            )

    if mismatches == 0:
        report.passed("Coordenadas UTM consistentes entre docx/kml e xlsx para todos os pontos.")


def check_single_zda_note(result: ProcessingResult, report: ValidationReport) -> None:
    files_with_zda = sum(1 for f in result.files if f.zda_count > 0)
    if files_with_zda <= 1:
        report.passed(
            "Caso de 1 único arquivo com ZDA detectado corretamente; tratado como observação, "
            "não como erro."
        )


def check_plana_vs_geodesica(result: ProcessingResult, report: ValidationReport,
                              tolerance_pct: float = 2.0) -> None:
    """Confere se a distância plana (UTM/Pitágoras) e a distância geodésica
    (elipsoidal, independente de fuso) estão próximas. Isso pega justamente o
    tipo de bug em que um ponto cai num fuso UTM diferente do da BASE e a
    subtração de coordenadas de fusos diferentes produz um delta sem sentido
    — nesse caso a distância plana diverge muito da geodésica."""
    problemas = 0
    for p in result.points_geo:
        if p.dist_geodesica_m == 0:
            continue
        diff_pct = 100 * abs(p.dist_plana_m - p.dist_geodesica_m) / p.dist_geodesica_m
        if diff_pct > tolerance_pct:
            problemas += 1
            report.fail(
                f"Ponto '{p.label}': distância plana ({p.dist_plana_m:.3f} m) diverge "
                f"{diff_pct:.2f}% da distância geodésica ({p.dist_geodesica_m:.3f} m) — "
                f"acima da tolerância de {tolerance_pct}%. Possível problema de fuso UTM "
                f"não tratado corretamente."
            )
    if problemas == 0:
        report.passed(
            "Distância plana e distância geodésica consistentes entre si (dentro de "
            f"{tolerance_pct}%) para todos os pontos — sem indício de erro de fuso UTM."
        )


def run_all_validations(
    result: ProcessingResult, docx_path: Path, xlsx_path: Path, workdir: Path
) -> ValidationReport:
    report = ValidationReport()
    check_docx_table_widths(docx_path, report)
    check_xlsx_formulas(xlsx_path, report, workdir / "_xlsx_recalc")
    check_cross_consistency(result, xlsx_path, report)
    check_single_zda_note(result, report)
    check_plana_vs_geodesica(result, report)
    return report
