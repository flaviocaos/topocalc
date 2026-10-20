"""Processamento GNSS do TopoCalc.

Fluxo:  .GNS  ->  RINEX (obs + nav)  ->  sessões / linhas de base
        ->  processamento (RTKLIB)  ->  ajustamento em rede  ->  coordenadas finais
"""
