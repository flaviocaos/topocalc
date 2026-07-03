"""
assets.py
=========
Localiza os arquivos de identidade visual (logo) do TopoCalc, empacotados
em `assets/` na raiz do projeto (irmã do pacote `topocalc/`).
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

LOGO_PNG = ASSETS_DIR / "topocalc_logo.png"
LOGO_SVG = ASSETS_DIR / "topocalc_logo.svg"
ICON_PNG = ASSETS_DIR / "topocalc_icon.png"
ICON_SVG = ASSETS_DIR / "topocalc_icon.svg"


def logo_png_path() -> Path | None:
    """Retorna o caminho do logo em PNG, ou None se o asset não estiver presente
    (o app deve continuar funcionando normalmente sem o logo)."""
    return LOGO_PNG if LOGO_PNG.exists() else None


def icon_png_path() -> Path | None:
    return ICON_PNG if ICON_PNG.exists() else None
