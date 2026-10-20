"""
cli.py
======
Linha de comando do TopoCalc.

  # 1) ver o que há nos arquivos (nomes sugeridos, alturas, sessões, linhas)
  python -m topocalc.cli analisar -i dados/*.GNS -p ./projeto

  # 2) processar, informando controle e correções de nome/altura
  python -m topocalc.cli processar -p ./projeto \\
      --controle "HBD01;utm;683668.738;9067058.188;33S;656.4086;648.3203" \\
      --renomear HBD1=HBD01 --renomear HCB02=HCB2 \\
      --altura HCB22810.GNS=1.694

  # (atalho: analisar + processar de uma vez)
  python -m topocalc.cli processar -i dados/*.GNS -p ./projeto --controle ...

  # 3) excluir vetores e reajustar sem reprocessar
  python -m topocalc.cli reajustar -p ./projeto --excluir HC102810-HCB8

Controle: "NOME;utm;E;N;FUSO+HEMISFÉRIO;h;H" ou "NOME;geo;LAT;LON;h;H"
(h elipsoidal; H ortométrica opcional — deixe vazio se não tiver).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .gnss import project as P


def _show_analysis(p: dict) -> None:
    print(f"\n{len(p['occupations'])} arquivo(s):")
    print(f"  {'arquivo':<16} {'marco':<8} {'dia':<10} {'início':>5}-{'fim':<5} {'antena':>6}  receptor")
    for o in p["occupations"]:
        print(f"  {o['file']:<16} {o['name']:<8} {o['day']:<10} {str(o['first'])[11:16]:>5}-"
              f"{str(o['last'])[11:16]:<5} {o['antenna_height']:6.3f}  {o['receiver']} ({o['format']})")
    print("\nLinhas de base propostas:")
    occ = {o["id"]: o for o in p["occupations"]}
    for b in p["baselines"]:
        L = f"{b['approx_length_m'] / 1000:7.2f} km" if b.get("approx_length_m") else "      ?"
        print(f"  [{'x' if b['include'] else ' '}] {b['id']:<26} {occ[b['occ_a']]['name']:>7} – "
              f"{occ[b['occ_b']]['name']:<7} {L}  {b['overlap_min']:5.0f} min")
    for s in p.get("suggestions", []):
        print("  ! " + s)


def _parse_control_arg(s: str) -> dict:
    f = [x.strip() for x in s.split(";")]
    if len(f) < 2:
        raise ValueError(f"Controle inválido: {s}")
    if f[1].lower() == "utm":
        zone = f[4]
        hem = zone[-1].upper() if zone[-1].isalpha() else "S"
        zone = zone.rstrip("SNsn")
        return {"name": f[0], "system": "utm", "E": float(f[2]), "N": float(f[3]), "zone": int(zone),
                "hemisphere": hem, "h": f[5] if len(f) > 5 else None, "H": f[6] if len(f) > 6 else None}
    return {"name": f[0], "system": "geo", "lat": f[2], "lon": f[3],
            "h": f[4] if len(f) > 4 else None, "H": f[5] if len(f) > 5 else None}


def cmd_analisar(args) -> int:
    pdir = args.projeto
    if args.input:
        missing = [str(x) for x in args.input if not x.exists()]
        if missing:
            print("Arquivos não encontrados: " + ", ".join(missing), file=sys.stderr)
            return 1
        P.create(pdir, [x.resolve() for x in args.input], [x.resolve() for x in args.precisas or []],
                 title=args.titulo or "")
        print("Convertendo para RINEX e analisando…")
        P.analyze(pdir)
    p = P.load(pdir)
    if p["status"] == "error":
        print("ERRO: " + p["message"], file=sys.stderr)
        return 1
    _show_analysis(p)
    return 0


def cmd_processar(args) -> int:
    pdir = args.projeto
    if args.input:
        rc = cmd_analisar(args)
        if rc:
            return rc
    p = P.load(pdir)
    ren = dict(x.split("=", 1) for x in args.renomear or [])
    alt = dict(x.split("=", 1) for x in args.altura or [])
    for o in p["occupations"]:
        if o["file"] in ren:
            o["name"] = ren[o["file"]].upper()
        elif o["name"] in ren:
            o["name"] = ren[o["name"]].upper()
        for key in (o["file"], o["name"]):
            if key in alt:
                o["antenna_height"] = float(alt[key].replace(",", "."))
    if args.controle:
        p["controls"] = [P.parse_control(_parse_control_arg(c)) for c in args.controle]
    if args.intervalo:
        p["settings"]["interval_s"] = args.intervalo
    if args.fuso:
        p["settings"]["utm_zone"] = int(args.fuso.rstrip("SNsn"))
        p["settings"]["hemisphere"] = args.fuso[-1].upper() if args.fuso[-1].isalpha() else None
    for b in p["baselines"]:
        if b["id"] in (args.sem_linha or []):
            b["include"] = False
        b["result"] = None
        b["excluded"] = False
    P.save(pdir, p)

    import threading
    import time
    t = threading.Thread(target=P.process, args=(pdir,))
    t.start()
    seen = 0
    while t.is_alive():
        time.sleep(1)
        seen = _print_log(pdir, seen)
    _print_log(pdir, seen)
    return _summary(pdir)


def _print_log(pdir: Path, seen: int) -> int:
    try:
        log = P.load(pdir).get("log", [])
    except Exception:  # noqa: BLE001 — arquivo sendo regravado
        return seen
    for ln in log[seen:]:
        print("  " + ln)
    return len(log)


def _summary(pdir: Path) -> int:
    p = P.load(pdir)
    if p["status"] != "done":
        print("ERRO: " + p.get("message", ""), file=sys.stderr)
        return 1
    pts = p["results"]["points"]
    print(f"\nCoordenadas finais (UTM {pts[0]['zone']}{pts[0]['hemisphere']}):")
    print(f"  {'ponto':<8} {'E':>12} {'N':>13} {'h':>9} {'H':>9}  σE    σN    σh  (mm)")
    for pt in pts:
        print(f"  {pt['name']:<8} {pt['E']:12.3f} {pt['N']:13.3f} {pt['h']:9.3f} {pt['H']:9.3f} "
              f"{pt['sE'] * 1000:5.0f} {pt['sN'] * 1000:5.0f} {pt['sU'] * 1000:5.0f}")
    flagged = [o for o in p["results"]["observations"] if o["flag"] in ("rejeitar", "atenção")]
    for o in flagged:
        print(f"  ! {o['id']} ({o['base']} → {o['rover']}): |w| = {o['w_max']:.2f} — {o['flag']}")
    print("\nEntregáveis:")
    for f in p.get("outputs", []):
        print("  " + f)
    return 0


def cmd_reajustar(args) -> int:
    pdir = args.projeto
    p = P.load(pdir)
    excl = set(args.excluir or [])
    for b in p["baselines"]:
        if b.get("result"):
            b["excluded"] = b["id"] in excl
    P.save(pdir, p)
    P.readjust(pdir)
    return _summary(pdir)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="topocalc", description="Pós-processamento GNSS de arquivos .GNS (Hi-Target).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(s, need_input):
        s.add_argument("-p", "--projeto", type=Path, required=True, help="pasta do projeto")
        s.add_argument("-i", "--input", type=Path, nargs="+", required=need_input, help="arquivos .GNS")
        s.add_argument("--precisas", type=Path, nargs="*", help="órbitas/relógios precisos (SP3, CLK)")
        s.add_argument("--titulo", help="nome do levantamento")

    a = sub.add_parser("analisar", help="converte e mostra arquivos, marcos e linhas de base")
    common(a, True)
    a.set_defaults(fn=cmd_analisar)

    pr = sub.add_parser("processar", help="processa linhas de base, ajusta e gera entregáveis")
    common(pr, False)
    pr.add_argument("--controle", action="append", help="marco de controle (ver ajuda do módulo)")
    pr.add_argument("--renomear", action="append", help="ANTIGO=NOVO (nome do marco ou do arquivo)")
    pr.add_argument("--altura", action="append", help="ARQUIVO_OU_MARCO=altura_m")
    pr.add_argument("--sem-linha", action="append", help="id de linha de base a não processar")
    pr.add_argument("--intervalo", type=int, help="intervalo de processamento em s (padrão 10)")
    pr.add_argument("--fuso", help="fuso UTM de saída, ex.: 33S (padrão automático)")
    pr.set_defaults(fn=cmd_processar)

    r = sub.add_parser("reajustar", help="exclui vetores e reajusta sem reprocessar")
    r.add_argument("-p", "--projeto", type=Path, required=True)
    r.add_argument("--excluir", nargs="*", help="ids das linhas de base a excluir")
    r.set_defaults(fn=cmd_reajustar)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
