"""
rt27.py
=======
Decodificador do registro Trimble RAWDATA 57h, tipo 6 ("RT27", dados GNSS
de levantamento em tempo real), gravado pelos receptores Hi-Target com placa
Trimble, e escritor de RINEX 3.04 de observação.

Não existe decodificador aberto para RT27; o layout abaixo foi obtido por
engenharia reversa e validado contra geometria de satélites, posicionamento
por ponto e linhas de base processadas no Leica Infinity (diferenças < 1 cm):

  Pacote:  STX(02) STATUS TIPO(57) LEN  dados...  CHECKSUM ETX(03)
  Dados:   tipo-registro(6)  página<<4|total  nº-resposta  flags
  Época:   len, semana GPS (u16 BE), ms da semana (u32 BE), relógio (i32),
           nº de satélites, ...; depois um cabeçalho estendido (len, ...).
  Satélite: len(12), PRN, sistema (0 GPS, 2 GLONASS, 3 Galileo, 10 BeiDou),
           canal / nº de frequência GLONASS (i8), nº de blocos, elev., azim. ...
  1º bloco: len(17), sinal, rastreio, flags, SNR*4, PR (u32 BE, 1/128 m),
           fase (s48 BE, 1/32768 ciclo, sinal invertido), contador de
           perda de ciclo, flags.
  Demais:  len(15), sinal, rastreio, flags, SNR*4, (PRk - PR1) s16 BE em
           1/256 m, fase s48, contador de perda de ciclo, flags.

O pseudorange de 32 bits estoura em 33 554 432 m (satélites geoestacionários
do BeiDou); a ambiguidade é resolvida com a fase da portadora.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from .gns_file import DATA_OFFSET

C_LIGHT = 299_792_458.0
PR_WRAP = 33_554_432.0
GPS_EPOCH = datetime(1980, 1, 6)

# (sistema, sinal, rastreio) -> código RINEX
SIGNAL_MAP = {
    (0, 0, 0): "1C", (0, 1, 2): "2W", (0, 2, 8): "5Q",
    (2, 0, 0): "1C", (2, 1, 1): "2P",
    (3, 0, 23): "1C", (3, 2, 11): "5Q", (3, 3, 11): "7Q",
    (10, 6, 26): "2I",
}
SYS_LETTER = {0: "G", 2: "R", 3: "E", 10: "C"}
OBS_CODES = {"G": ["1C", "2W", "5Q"], "R": ["1C", "2P"], "E": ["1C", "5Q", "7Q"], "C": ["2I"]}


def _freq(sysid: int, sig: int, k: int) -> Optional[float]:
    if sysid == 0:
        return {0: 1575.42e6, 1: 1227.6e6, 2: 1176.45e6}.get(sig)
    if sysid == 2:
        return {0: 1602e6 + k * 0.5625e6, 1: 1246e6 + k * 0.4375e6}.get(sig)
    if sysid == 3:
        return {0: 1575.42e6, 2: 1176.45e6, 3: 1207.14e6}.get(sig)
    if sysid == 10:
        return {6: 1561.098e6}.get(sig)
    return None


def _s48(b: bytes) -> int:
    v = int.from_bytes(b, "big")
    return v - (1 << 48) if v >> 47 else v


def iter_packets(d: bytes, start: int = DATA_OFFSET) -> Iterator[tuple[int, bytes]]:
    n = len(d)
    i = start
    while i < n - 4:
        if d[i] != 0x02:
            j = d.find(b"\x02", i + 1)
            if j < 0:
                return
            i = j
            continue
        ln = d[i + 3]
        end = i + 4 + ln
        if end + 2 <= n and d[end + 1] == 0x03 and (sum(d[i + 1:end]) & 0xFF) == d[end]:
            yield d[i + 2], d[i + 4:end]
            i = end + 2
            continue
        i += 1


def _parse_epoch(b: bytes) -> dict:
    hl = b[0]
    week, ms = struct.unpack_from(">HI", b, 1)
    i = hl
    i += b[i]  # cabeçalho estendido
    sats = []
    while i < len(b):
        sl = b[i]
        if sl == 0 or i + sl > len(b):
            break
        h = b[i:i + sl]
        prn, sysid, ch, nb = h[1], h[2], h[3], h[4]
        i += sl
        blks = []
        for k in range(nb):
            if i >= len(b):
                break
            bl = b[i]
            x = b[i:i + bl]
            i += bl
            if bl < 15:
                continue
            if k == 0:
                if bl < 17:
                    continue
                blks.append(dict(sig=x[1], trk=x[2], snr=x[4] / 4,
                                 pr=int.from_bytes(x[5:9], "big") / 128.0,
                                 ph=_s48(x[9:15]) / 32768.0, slip=x[15]))
            else:
                blks.append(dict(sig=x[1], trk=x[2], snr=x[4] / 4,
                                 dpr=struct.unpack_from(">h", x, 5)[0],
                                 ph=_s48(x[7:13]) / 32768.0, slip=x[13]))
        sats.append(dict(prn=prn, sys=sysid, ch=ch - 256 if ch > 127 else ch, blks=blks))
    return dict(week=week, ms=ms, sats=sats)


def iter_epochs(d: bytes) -> Iterator[dict]:
    buf = None
    expected = 0
    reply = None
    for ptype, p in iter_packets(d):
        if ptype != 0x57 or len(p) < 4 or p[0] != 6:
            continue
        page, total = p[1] >> 4, p[1] & 0x0F
        if page == 1:
            buf = bytearray(p[4:])
            reply = p[2]
        elif buf is not None and page == expected and p[2] == reply:
            buf += p[4:]
        else:
            buf = None
            continue
        expected = page + 1
        if page == total:
            try:
                yield _parse_epoch(bytes(buf))
            except (struct.error, IndexError):
                pass
            buf = None


def count_epochs(d: bytes) -> int:
    return sum(1 for _ in iter_epochs(d))


def _hdr(o, txt: str, label: str) -> None:
    o.write("%-60s%-20s\n" % (txt[:60], label))


def write_rinex_obs(gns_path: Path, out_path: Path, marker: str,
                    antenna_height: float = 0.0, receiver: str = "HI-TARGET",
                    serial: str = "", lli: bool = True) -> dict:
    """Converte um .GNS com dados RT27 em RINEX 3.04 de observação.

    lli=False omite os indicadores de perda de ciclo do receptor. O contador
    de perda de ciclo do RT27 muda muitas vezes sem salto real na fase; para o
    processamento é melhor deixar o RTKLIB detectar as perdas (GF/MW).
    """
    d = gns_path.read_bytes()

    def gtime(e):
        return GPS_EPOCH + timedelta(weeks=e["week"], milliseconds=e["ms"])

    # 1ª passada (sem guardar as épocas na memória): período, intervalo e
    # números de frequência GLONASS para o cabeçalho
    glo: dict = {}
    t0 = t1 = t_second = None
    for e in iter_epochs(d):
        t = gtime(e)
        if t0 is None:
            t0 = t
        elif t_second is None and t > t0:
            t_second = t
        t1 = t
        for s in e["sats"]:
            if s["sys"] == 2:
                glo[s["prn"]] = s["ch"]
    if t0 is None:
        raise ValueError(f"{gns_path.name}: nenhuma época RT27 decodificada.")
    last_slip: dict = {}
    n_written = 0
    with open(out_path, "w", newline="\n") as o:
        _hdr(o, "     3.04           OBSERVATION DATA    M (MIXED)", "RINEX VERSION / TYPE")
        _hdr(o, "%-20s%-20s%s" % ("TopoCalc", "", datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S UTC")),
             "PGM / RUN BY / DATE")
        _hdr(o, "Convertido de .GNS (Trimble RT27) pelo TopoCalc", "COMMENT")
        _hdr(o, marker, "MARKER NAME")
        _hdr(o, "", "MARKER NUMBER")
        _hdr(o, "GEODETIC", "MARKER TYPE")
        _hdr(o, "", "OBSERVER / AGENCY")
        _hdr(o, "%-20s%-20s%-20s" % (serial[:20], receiver[:20], ""), "REC # / TYPE / VERS")
        _hdr(o, "%-20s%-20s" % ("", ""), "ANT # / TYPE")
        _hdr(o, "%14.4f%14.4f%14.4f" % (0, 0, 0), "APPROX POSITION XYZ")
        _hdr(o, "%14.4f%14.4f%14.4f" % (antenna_height, 0, 0), "ANTENNA: DELTA H/E/N")
        for sy in "GREC":
            obs = []
            for cd in OBS_CODES[sy]:
                obs += ["C" + cd, "L" + cd, "S" + cd]
            _hdr(o, "%s  %3d " % (sy, len(obs)) + " ".join(obs), "SYS / # / OBS TYPES")
        if t_second is not None:
            _hdr(o, "%10.3f" % (t_second - t0).total_seconds(), "INTERVAL")
        for lab, t in (("TIME OF FIRST OBS", t0), ("TIME OF LAST OBS", t1)):
            _hdr(o, "  %4d    %02d    %02d    %02d    %02d   %10.7f     GPS"
                 % (t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6), lab)
        gl = sorted(glo.items())
        line = "%3d" % len(gl)
        for i, (p, k) in enumerate(gl):
            if i and i % 8 == 0:
                _hdr(o, line, "GLONASS SLOT / FRQ #")
                line = "   "
            line += " R%02d %2d" % (p, k)
        _hdr(o, line, "GLONASS SLOT / FRQ #")
        _hdr(o, " C1C    0.000 C1P    0.000 C2C    0.000 C2P    0.000", "GLONASS COD/PHS/BIS")
        _hdr(o, "", "END OF HEADER")

        for e in iter_epochs(d):
            tt = gtime(e)
            recs = []
            for s in e["sats"]:
                sy = SYS_LETTER.get(s["sys"])
                if not sy or not s["blks"]:
                    continue
                k = glo.get(s["prn"], 0) if sy == "R" else 0
                b0 = s["blks"][0]
                if "pr" not in b0:
                    continue
                pr0 = b0["pr"]
                f0 = _freq(s["sys"], b0["sig"], k)
                if b0["ph"] != 0 and f0:
                    rr = -b0["ph"] * C_LIGHT / f0
                    pr0 += round((rr - pr0) / PR_WRAP) * PR_WRAP
                vals = {}
                for j, b in enumerate(s["blks"]):
                    cd = SIGNAL_MAP.get((s["sys"], b["sig"], b["trk"]))
                    if not cd:
                        continue
                    pr = pr0 if j == 0 else pr0 + b.get("dpr", 0) / 256.0
                    ph = None if b["ph"] == 0 else -b["ph"]
                    key = (sy, s["prn"], cd)
                    flag = 1 if (lli and key in last_slip and last_slip[key] != b["slip"]) else 0
                    last_slip[key] = b["slip"]
                    if not (0 < pr < 1e9):
                        continue
                    vals[cd] = (pr, ph, flag, b["snr"])
                if not vals:
                    continue
                line = "%s%02d" % (sy, s["prn"])
                for cd in OBS_CODES[sy]:
                    if cd in vals:
                        pr, ph, flag, snr = vals[cd]
                        line += "%14.3f  " % pr
                        line += ("%14.3f%1d " % (ph, flag)) if ph is not None and abs(ph) < 1e10 else " " * 16
                        line += "%14.3f  " % snr
                    else:
                        line += " " * 48
                recs.append(line.rstrip())
            if not recs:
                continue
            o.write("> %4d %02d %02d %02d %02d%11.7f  0%3d\n"
                    % (tt.year, tt.month, tt.day, tt.hour, tt.minute,
                       tt.second + tt.microsecond / 1e6, len(recs)))
            for r in recs:
                o.write(r + "\n")
            n_written += 1

    return {"first": t0, "last": t1, "epochs": n_written, "glonass_slots": dict(gl)}
