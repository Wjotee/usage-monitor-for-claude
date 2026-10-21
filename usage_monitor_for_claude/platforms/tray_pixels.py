"""
Tray Pixel Dispatch
====================

Selects the exact-size tray icon backend for the running system.

Kept out of :mod:`usage_monitor_for_claude.platforms` so the crisp tray
renderer stays a self-contained addition: nothing in the platform package's
``__init__`` has to change to offer it.
"""
from __future__ import annotations

import sys

if sys.platform == 'win32':
    from .tray_pixels_win32 import install_exact_icon_handle, load_clock_font, tray_icon_size
else:
    from .tray_pixels_linux import install_exact_icon_handle, load_clock_font, tray_icon_size

__all__ = ['install_exact_icon_handle', 'load_clock_font', 'tray_icon_size']
