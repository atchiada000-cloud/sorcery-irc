"""Colour palette shared by the desktop apps: the current Omarchy theme when
there is one (Linux), otherwise Omarchy's "Everforest"."""

from __future__ import annotations

import tomllib
from pathlib import Path

THEME_STATE = Path.home() / ".local" / "state" / "omarchy" / "current"

FALLBACK = {
    "mode": "dark", "accent": "#7fbbb3", "selection": "#3d484d", "muted": "#475258",
    "background": "#2d353b", "dark_background": "#21272c", "darker_background": "#181d20",
    "lighter_background": "#343f44", "foreground": "#d3c6aa", "light_foreground": "#9da9a0",
    "red": "#e67e80", "yellow": "#dbbc7f", "orange": "#e09d7f", "green": "#a7c080",
    "cyan": "#83c092", "blue": "#7fbbb3", "magenta": "#d699b6",
}
# Colours for nick0 .. nick6 (see core.nick_tag).
NICK_PALETTE = ["red", "green", "yellow", "blue", "magenta", "cyan", "orange"]


def load_theme() -> dict:
    colours = dict(FALLBACK)
    try:
        with open(THEME_STATE / "theme" / "colors.toml", "rb") as f:
            colours.update({k: v for k, v in tomllib.load(f).items() if isinstance(v, str)})
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return colours
