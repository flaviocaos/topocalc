"""
cli.py
======
Interface de linha de comando do TopoCalc.

Uso:
    python -m topocalc.cli --input arquivo1.gns arquivo2.gns arquivo3.gns \
        --output ./saida [--base arquivo1.gns] [--skip-pdf]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import run_pipeline
from .docx_report import build_docx_report
from .pdf_report import convert_docx_to_pdf, PdfConversionError
from .kml_export import export_kml
from .xlsx_export import export_xlsx
from .validate import run_all_validations


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topocalc",
        description="TopoCalc — processa arquivos .GNS (Hi-Target/Champion) e gera pacote de "
                     "entregáveis geodésicos (docx, pdf, kml, xlsx).",
    )
    parser.add_argument("--input", "-i", nargs="+", required=True, type=Path,
                         help="Lista de arquivos .GNS de entrada (mínimo 2: 1 base + 1 ponto).")
    parser.add_argument("--output", "-o", type=Path, default=Path("./saida_gns"),
                         help="Diretório de saída para os entregáveis (padrão: ./saida_gns).")
    parser.add_argument("--base", type=str, default=None,
                         help="Nome do arquivo (dentro de --input) a ser tratado como BASE. "
                              "Se omitido, detecta automaticamente pela contagem de sentenças ZDA.")
    parser.add_argument("--skip-pdf", action="store_true",
                         help="Não gerar o PDF (útil se LibreOffice não estiver disponível).")
    parser.add_argument("--skip-validation", action="store_true",
                         help="Não executar as validações automáticas ao final.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    for p in args.input:
        if not p.exists():
            print(f"ERRO: arquivo de entrada não encontrado: {p}", file=sys.stderr)
            return 1

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 52)
    print("  TopoCalc — Pacote Geodésico a partir do .GNS")
    print("=" * 52)
    print(f"[1/6] Lendo e interpretando {len(args.input)} arquivo(s) .GNS...")
    try:
        result = run_pipeline(args.input, forced_base_name=args.base)
    except Exception as e:
        print(f"ERRO no processamento: {e}", file=sys.stderr)
        return 1

    print(f"      BASE detectada: {result.base_file.path.name} (label {result.base_geo.label})")
    for f in result.point_files:
        print(f"      Ponto: {f.path.name} (label {f.label})")

    print("[2/6] Gerando relatório .docx...")
    docx_path = build_docx_report(result, output_dir)
    print(f"      -> {docx_path}")

    pdf_path = None
    if not args.skip_pdf:
        print("[3/6] Convertendo relatório para .pdf (LibreOffice headless)...")
        try:
            pdf_path = convert_docx_to_pdf(docx_path, output_dir)
            print(f"      -> {pdf_path}")
        except PdfConversionError as e:
            print(f"      AVISO: não foi possível gerar PDF: {e}", file=sys.stderr)
    else:
        print("[3/6] Geração de PDF pulada (--skip-pdf).")

    print("[4/6] Gerando KML...")
    kml_path = export_kml(result, output_dir / "pontos_levantamento.kml")
    print(f"      -> {kml_path}")

    print("[5/6] Gerando planilha .xlsx...")
    xlsx_path = export_xlsx(result, output_dir / "planilha_geodesica.xlsx")
    print(f"      -> {xlsx_path}")

    if not args.skip_validation:
        print("[6/6] Executando validações automáticas...")
        report = run_all_validations(result, docx_path, xlsx_path, output_dir)
        for msg in report.checks_passed:
            print(f"      OK  - {msg}")
        for msg in report.issues:
            print(f"      FALHA - {msg}", file=sys.stderr)
        if not report.ok:
            print("\nATENÇÃO: uma ou mais validações falharam. Revise os itens acima.", file=sys.stderr)
            return 2
        print("      Todas as validações passaram.")
    else:
        print("[6/6] Validação pulada (--skip-validation).")

    print(f"\nConcluído. Entregáveis em: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
