"""
charts.py
=========
Gera as figuras usadas no relatório: croqui de distribuição espacial e
gráfico de barras de distâncias em relação à base.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .pipeline import ProcessingResult


def plot_croqui(result: ProcessingResult, output_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(6.5, 6.5), dpi=150)

    base = result.base_geo
    ax.scatter([base.utm_easting], [base.utm_northing], c="red", marker="^", s=140, label="BASE", zorder=3)
    ax.annotate(base.label, (base.utm_easting, base.utm_northing), textcoords="offset points",
                xytext=(8, 8), fontsize=9, fontweight="bold")

    for p in result.points_geo:
        ax.scatter([p.utm_easting], [p.utm_northing], c="royalblue", marker="o", s=90, zorder=3)
        ax.annotate(p.label, (p.utm_easting, p.utm_northing), textcoords="offset points",
                    xytext=(8, 8), fontsize=9)
        ax.plot([base.utm_easting, p.utm_easting], [base.utm_northing, p.utm_northing],
                linestyle="--", color="gray", linewidth=1, zorder=1)

    ax.set_xlabel(f"Este UTM (m) — Zona {base.utm_zone}{base.utm_hemisphere}")
    ax.set_ylabel("Norte UTM (m)")
    ax.set_title("Croqui de distribuição espacial dos pontos")
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.set_aspect("equal", adjustable="datalim")
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_distancias(result: ProcessingResult, output_path: Path) -> Path:
    labels = [p.label for p in result.points_geo]
    dist_geo = [p.dist_geodesica_m for p in result.points_geo]
    dist_plana = [p.dist_plana_m for p in result.points_geo]

    x = range(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=150)
    ax.bar([i - width / 2 for i in x], dist_plana, width, label="Distância plana (m)", color="steelblue")
    ax.bar([i + width / 2 for i in x], dist_geo, width, label="Distância geodésica (m)", color="darkorange")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Distância (m)")
    ax.set_title("Distância de cada ponto em relação à BASE")
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path
