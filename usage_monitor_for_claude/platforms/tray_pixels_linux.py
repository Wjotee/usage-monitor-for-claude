"""
Tray Pixels (Linux)
====================

Linux counterpart of :mod:`usage_monitor_for_claude.platforms.tray_pixels_win32`.

A StatusNotifierItem hands the panel an image and the panel decides how large
to draw it, so there is no fixed slot size to render for and no icon handle
to build.  The functions report that, and the original renderer stays in use.
"""
from __future__ import annotations

from typing import Any

from PIL import ImageFont

__all__ = ['install_exact_icon_handle', 'load_clock_font', 'tray_icon_size']


def tray_icon_size() -> int | None:
    """Return None: the panel scales the icon itself, so no exact size exists."""
    return None


def install_exact_icon_handle(icon: Any) -> bool:
    """Return False: pystray's AppIndicator backend has no icon handle to replace."""
    return False


def load_clock_font(pixel_size: float) -> ImageFont.FreeTypeFont | None:
    """Return None: panels have no single clock typeface, and the exact-size renderer never runs here."""
    return None
