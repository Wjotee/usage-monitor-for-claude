"""
Tray Pixels (Windows)
======================

Tells the renderer how many pixels the notification area draws a tray icon
at, and hands the rendered pixels to Windows without any resampling.

pystray serializes the image to a temporary ``.ico`` and loads it with
``LoadImage(..., LR_DEFAULTSIZE)``.  Pillow writes only the standard ICO frame
sizes (16, 24, 32, ...) and Lanczos-shrinks the image into each, and
``LR_DEFAULTSIZE`` then loads the *large* icon size (32 px at 100% scaling),
which the taskbar shrinks again into its 16 px slot.  Every edge ends up on a
fractional pixel.  :func:`install_exact_icon_handle` replaces that path with
``CreateIconIndirect`` on the image's own pixels, so an image drawn at
:func:`tray_icon_size` reaches the screen one-to-one.

:func:`load_clock_font` supplies the taskbar clock's typeface for the digits.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import traceback
from typing import Any

from PIL import Image, ImageFont

__all__ = ['install_exact_icon_handle', 'load_clock_font', 'tray_icon_size']

# Segoe UI regular - the typeface of the Windows 10 taskbar clock
_CLOCK_FONT_FILE = 'segoeui.ttf'
_SM_CXSMICON = 49
_BI_RGB = 0
_DIB_RGB_COLORS = 0

# Private library handles: setting argtypes/restype on the shared
# ``ctypes.windll`` objects would change them for every other caller.
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ('fIcon', ctypes.wintypes.BOOL),
        ('xHotspot', ctypes.wintypes.DWORD),
        ('yHotspot', ctypes.wintypes.DWORD),
        ('hbmMask', ctypes.wintypes.HBITMAP),
        ('hbmColor', ctypes.wintypes.HBITMAP),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', ctypes.wintypes.DWORD),
        ('biWidth', ctypes.wintypes.LONG),
        ('biHeight', ctypes.wintypes.LONG),
        ('biPlanes', ctypes.wintypes.WORD),
        ('biBitCount', ctypes.wintypes.WORD),
        ('biCompression', ctypes.wintypes.DWORD),
        ('biSizeImage', ctypes.wintypes.DWORD),
        ('biXPelsPerMeter', ctypes.wintypes.LONG),
        ('biYPelsPerMeter', ctypes.wintypes.LONG),
        ('biClrUsed', ctypes.wintypes.DWORD),
        ('biClrImportant', ctypes.wintypes.DWORD),
    ]


# Handle-returning functions need an explicit pointer-sized restype: the
# ctypes default c_int truncates a 64-bit handle.
_user32.FindWindowW.argtypes = (ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR)
_user32.FindWindowW.restype = ctypes.wintypes.HWND
_user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.CreateIconIndirect.argtypes = (ctypes.POINTER(_ICONINFO),)
_user32.CreateIconIndirect.restype = ctypes.wintypes.HICON
_gdi32.CreateBitmap.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.wintypes.UINT, ctypes.wintypes.UINT, ctypes.c_void_p)
_gdi32.CreateBitmap.restype = ctypes.wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = (
    ctypes.wintypes.HDC, ctypes.POINTER(_BITMAPINFOHEADER), ctypes.wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
)
_gdi32.CreateDIBSection.restype = ctypes.wintypes.HBITMAP
_gdi32.DeleteObject.argtypes = (ctypes.wintypes.HGDIOBJ,)
_gdi32.DeleteObject.restype = ctypes.wintypes.BOOL


def tray_icon_size() -> int:
    """Return the pixel size the notification area draws a tray icon at.

    That is the small-icon metric at the DPI of the monitor carrying the
    taskbar: 16 at 100% scaling, 20 at 125%, 24 at 150%, 32 at 200%.  The
    per-DPI APIs exist from Windows 10 1607; before that, and whenever the
    taskbar window cannot be found, the system-DPI metric is used.
    """
    get_metrics_for_dpi = getattr(_user32, 'GetSystemMetricsForDpi', None)
    dpi = _taskbar_dpi()
    if get_metrics_for_dpi is None or not dpi:
        return _user32.GetSystemMetrics(_SM_CXSMICON)

    get_metrics_for_dpi.argtypes = (ctypes.c_int, ctypes.wintypes.UINT)
    get_metrics_for_dpi.restype = ctypes.c_int

    return get_metrics_for_dpi(_SM_CXSMICON, dpi) or _user32.GetSystemMetrics(_SM_CXSMICON)


def load_clock_font(pixel_size: float) -> ImageFont.FreeTypeFont | None:
    """Return the taskbar clock's typeface at *pixel_size*, or None if it is not installed."""
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    try:
        return ImageFont.truetype(os.path.join(windir, 'Fonts', _CLOCK_FONT_FILE), pixel_size)
    except OSError:
        return None


def install_exact_icon_handle(icon: Any) -> bool:
    """Make a pystray icon pass its image to Windows at the image's own size.

    Replaces the icon's ``_assert_icon_handle`` - the method pystray calls
    before every ``Shell_NotifyIcon`` - with one that builds the handle via
    ``CreateIconIndirect``.  This also skips pystray's temporary ``.ico``
    file.  If building the handle fails, pystray's own method runs instead,
    so the icon degrades to the old rendering rather than disappearing.

    This reaches into pystray internals like the double-click handler in
    :mod:`usage_monitor_for_claude.platforms.win32`: a pystray release that
    renames ``_assert_icon_handle`` or ``_icon_handle`` is what would break
    it, which is why the swap reports whether it happened.

    Returns
    -------
    bool
        True if the method was replaced.
    """
    if not hasattr(icon, '_assert_icon_handle') or not hasattr(icon, '_icon_handle'):
        return False

    pystray_assert_icon_handle = icon._assert_icon_handle

    def assert_icon_handle() -> None:
        if icon._icon_handle:
            return
        handle = None
        try:
            handle = _icon_handle_from_image(icon.icon)
        except (OSError, ValueError):
            traceback.print_exc()
        if handle:
            icon._icon_handle = handle
        else:
            pystray_assert_icon_handle()

    icon._assert_icon_handle = assert_icon_handle

    return True


def _icon_handle_from_image(image: Image.Image) -> int:
    """Create an ``HICON`` from *image* with exactly its pixel size.

    The color bitmap is a top-down 32-bit DIB in straight (non-premultiplied)
    BGRA, which is what Windows expects for an alpha icon; the monochrome mask
    is all zeros because the alpha channel takes precedence.  The caller owns
    the handle and frees it with ``DestroyIcon`` (pystray does this in
    ``_release_icon``).

    Raises
    ------
    ValueError
        If the image has no pixels.
    OSError
        If Windows refuses to create one of the bitmaps or the icon.
    """
    rgba = image if image.mode == 'RGBA' else image.convert('RGBA')
    width, height = rgba.size
    if width <= 0 or height <= 0:
        raise ValueError(f'Cannot build an icon from a {width}x{height} image')

    header = _BITMAPINFOHEADER(
        biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=width, biHeight=-height,  # negative height: top-down rows
        biPlanes=1, biBitCount=32, biCompression=_BI_RGB,
    )
    bits = ctypes.c_void_p()
    color_bitmap = _gdi32.CreateDIBSection(None, ctypes.byref(header), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not color_bitmap or not bits.value:
        raise ctypes.WinError(ctypes.get_last_error())

    mask_bitmap = None
    try:
        pixels = rgba.tobytes('raw', 'BGRA')
        ctypes.memmove(bits.value, pixels, len(pixels))

        # Monochrome bitmap rows are padded to 16-bit boundaries.
        mask_stride = (width + 15) // 16 * 2
        mask_bits = ctypes.create_string_buffer(mask_stride * height)
        mask_bitmap = _gdi32.CreateBitmap(width, height, 1, 1, mask_bits)
        if not mask_bitmap:
            raise ctypes.WinError(ctypes.get_last_error())

        icon_info = _ICONINFO(fIcon=True, xHotspot=0, yHotspot=0, hbmMask=mask_bitmap, hbmColor=color_bitmap)
        handle = _user32.CreateIconIndirect(ctypes.byref(icon_info))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        # CreateIconIndirect copies both bitmaps, so they are freed either way.
        _gdi32.DeleteObject(color_bitmap)
        if mask_bitmap:
            _gdi32.DeleteObject(mask_bitmap)

    return handle


def _taskbar_dpi() -> int:
    """Return the DPI of the monitor carrying the taskbar, or 0 if unknown."""
    get_dpi_for_window = getattr(_user32, 'GetDpiForWindow', None)
    if get_dpi_for_window is None:
        return 0

    get_dpi_for_window.argtypes = (ctypes.wintypes.HWND,)
    get_dpi_for_window.restype = ctypes.wintypes.UINT
    taskbar = _user32.FindWindowW('Shell_TrayWnd', None)
    if not taskbar:
        return 0

    return get_dpi_for_window(taskbar)
