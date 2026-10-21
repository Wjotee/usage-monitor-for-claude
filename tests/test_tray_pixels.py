"""
Tray Pixels Tests
==================

Unit tests for the exact-size tray icon platform layer.

The Windows tests build real icon handles and draw them through GDI: the
point of the module is that Windows receives the pixels unscaled, and a mock
could only prove the mock.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import usage_monitor_for_claude.crisp_tray_icon as crisp_mod
from usage_monitor_for_claude.platforms import tray_pixels_linux

if sys.platform == 'win32':
    import ctypes
    import ctypes.wintypes

    from usage_monitor_for_claude.platforms import tray_pixels_win32

    # Private library handles with full prototypes: handles are 64-bit, and
    # the shared ctypes.windll objects must not be changed for other tests.
    _user32 = ctypes.WinDLL('user32')
    _gdi32 = ctypes.WinDLL('gdi32')

    class _ICONINFO(ctypes.Structure):
        _fields_ = [
            ('fIcon', ctypes.wintypes.BOOL), ('xHotspot', ctypes.wintypes.DWORD), ('yHotspot', ctypes.wintypes.DWORD),
            ('hbmMask', ctypes.wintypes.HBITMAP), ('hbmColor', ctypes.wintypes.HBITMAP),
        ]

    class _BITMAP(ctypes.Structure):
        _fields_ = [
            ('bmType', ctypes.wintypes.LONG), ('bmWidth', ctypes.wintypes.LONG), ('bmHeight', ctypes.wintypes.LONG),
            ('bmWidthBytes', ctypes.wintypes.LONG), ('bmPlanes', ctypes.wintypes.WORD), ('bmBitsPixel', ctypes.wintypes.WORD),
            ('bmBits', ctypes.c_void_p),
        ]

    _user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    _user32.GetSystemMetricsForDpi.argtypes = (ctypes.c_int, ctypes.wintypes.UINT)
    _user32.DestroyIcon.argtypes = (ctypes.wintypes.HICON,)
    _user32.GetIconInfo.argtypes = (ctypes.wintypes.HICON, ctypes.POINTER(_ICONINFO))
    _user32.DrawIconEx.argtypes = (
        ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.wintypes.HICON, ctypes.c_int, ctypes.c_int,
        ctypes.wintypes.UINT, ctypes.wintypes.HBRUSH, ctypes.wintypes.UINT,
    )
    _gdi32.CreateCompatibleDC.argtypes = (ctypes.wintypes.HDC,)
    _gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
    _gdi32.CreateDIBSection.argtypes = (
        ctypes.wintypes.HDC, ctypes.c_void_p, ctypes.wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
    )
    _gdi32.CreateDIBSection.restype = ctypes.wintypes.HBITMAP
    _gdi32.SelectObject.argtypes = (ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ)
    _gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ
    _gdi32.DeleteDC.argtypes = (ctypes.wintypes.HDC,)
    _gdi32.DeleteObject.argtypes = (ctypes.wintypes.HGDIOBJ,)
    _gdi32.GetObjectW.argtypes = (ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p)


def _destroy_icon(handle):
    _user32.DestroyIcon(handle)


class TestLinuxBackend(unittest.TestCase):
    """The Linux backend reports that no exact size or handle exists."""

    def test_no_slot_size(self):
        self.assertIsNone(tray_pixels_linux.tray_icon_size())

    def test_handle_not_installed(self):
        icon = MagicMock()

        self.assertFalse(tray_pixels_linux.install_exact_icon_handle(icon))
        icon._assert_icon_handle.assert_not_called()

    def test_no_clock_font(self):
        self.assertIsNone(tray_pixels_linux.load_clock_font(11))


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class TestClockFontLoader(unittest.TestCase):
    """The taskbar clock's typeface comes from the Windows fonts folder."""

    def test_loads_segoe_ui_at_requested_size(self):
        font = tray_pixels_win32.load_clock_font(11.5)

        self.assertTrue(font.path.lower().endswith('segoeui.ttf'))
        self.assertEqual(font.size, 11.5)

    def test_missing_font_returns_none(self):
        with patch.dict('os.environ', {'WINDIR': 'Z:\\no-such-windows'}):
            self.assertIsNone(tray_pixels_win32.load_clock_font(11))


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class TestTrayIconSize(unittest.TestCase):
    """The slot size follows the taskbar's DPI."""

    def test_size_is_small_icon_metric(self):
        size = tray_pixels_win32.tray_icon_size()

        self.assertGreaterEqual(size, 16)
        self.assertEqual(size, _user32.GetSystemMetricsForDpi(49, tray_pixels_win32._taskbar_dpi()))

    def test_size_per_scaling(self):
        """100% / 125% / 150% / 200% scaling map to 16 / 20 / 24 / 32 px."""
        for dpi, expected in ((96, 16), (120, 20), (144, 24), (192, 32)):
            with self.subTest(dpi=dpi), patch.object(tray_pixels_win32, '_taskbar_dpi', return_value=dpi):
                self.assertEqual(tray_pixels_win32.tray_icon_size(), expected)

    def test_unknown_dpi_falls_back_to_system_metric(self):
        with patch.object(tray_pixels_win32, '_taskbar_dpi', return_value=0):
            self.assertEqual(tray_pixels_win32.tray_icon_size(), _user32.GetSystemMetrics(49))


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class TestIconHandle(unittest.TestCase):
    """The handle carries the image's own pixels at the image's own size."""

    _DI_NORMAL = 0x0003

    def _draw_on_white(self, handle, size):
        """Draw *handle* at its own size onto a white 32-bit DIB and return the BGRA bytes."""
        header = tray_pixels_win32._BITMAPINFOHEADER(
            biSize=ctypes.sizeof(tray_pixels_win32._BITMAPINFOHEADER), biWidth=size, biHeight=-size, biPlanes=1, biBitCount=32,
        )
        bits = ctypes.c_void_p()
        dc = _gdi32.CreateCompatibleDC(None)
        bitmap = _gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        try:
            _gdi32.SelectObject(dc, bitmap)
            ctypes.memset(bits.value, 0xFF, size * size * 4)
            self.assertTrue(_user32.DrawIconEx(dc, 0, 0, handle, size, size, 0, None, self._DI_NORMAL))
            return ctypes.string_at(bits.value, size * size * 4)
        finally:
            _gdi32.DeleteDC(dc)
            _gdi32.DeleteObject(bitmap)

    @staticmethod
    def _icon_width(handle):
        info = _ICONINFO()
        _user32.GetIconInfo(handle, ctypes.byref(info))
        bitmap = _BITMAP()
        _gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap))
        _gdi32.DeleteObject(info.hbmColor)
        _gdi32.DeleteObject(info.hbmMask)
        return bitmap.bmWidth

    def test_pixels_reach_windows_unscaled(self):
        """A 16 px icon drawn at 16 px reproduces every pixel, alpha-blended onto white."""
        img = crisp_mod.create_icon_image(45, 60, light_taskbar=True, time_pct_top=30, time_pct_bottom=70, size=16)
        handle = tray_pixels_win32._icon_handle_from_image(img)
        self.addCleanup(_destroy_icon, handle)

        drawn = self._draw_on_white(handle, 16)
        for y in range(16):
            for x in range(16):
                red, green, blue, alpha = img.getpixel((x, y))
                offset = (y * 16 + x) * 4
                expected = tuple(round(channel * alpha / 255 + 255 * (1 - alpha / 255)) for channel in (blue, green, red))
                actual = tuple(drawn[offset:offset + 3])
                for expected_channel, actual_channel in zip(expected, actual):
                    self.assertLessEqual(abs(expected_channel - actual_channel), 1, f'Pixel ({x}, {y}): {actual} != {expected}')

    def test_handle_size_matches_image(self):
        for size in (16, 20, 24, 32):
            with self.subTest(size=size):
                handle = tray_pixels_win32._icon_handle_from_image(Image.new('RGBA', (size, size), (255, 0, 0, 255)))
                self.addCleanup(_destroy_icon, handle)
                self.assertEqual(self._icon_width(handle), size)

    def test_non_rgba_image_converted(self):
        handle = tray_pixels_win32._icon_handle_from_image(Image.new('RGB', (16, 16), (0, 0, 0)))
        self.addCleanup(_destroy_icon, handle)

        self.assertTrue(handle)

    def test_empty_image_rejected(self):
        with self.assertRaises(ValueError):
            tray_pixels_win32._icon_handle_from_image(Image.new('RGBA', (0, 0)))


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class TestInstallExactIconHandle(unittest.TestCase):
    """The swap of pystray's handle loader."""

    def _fake_icon(self, image):
        icon = MagicMock()
        icon._icon_handle = None
        icon.icon = image
        return icon

    def test_replaces_loader_and_builds_handle(self):
        icon = self._fake_icon(crisp_mod.create_icon_image(45, 20, size=16))
        pystray_loader = icon._assert_icon_handle

        self.assertTrue(tray_pixels_win32.install_exact_icon_handle(icon))
        icon._assert_icon_handle()
        self.addCleanup(_destroy_icon, icon._icon_handle)

        self.assertTrue(icon._icon_handle)
        pystray_loader.assert_not_called()

    def test_existing_handle_kept(self):
        icon = self._fake_icon(crisp_mod.create_icon_image(45, 20, size=16))
        tray_pixels_win32.install_exact_icon_handle(icon)
        icon._icon_handle = 1234

        with patch.object(tray_pixels_win32, '_icon_handle_from_image') as build:
            icon._assert_icon_handle()

        build.assert_not_called()
        self.assertEqual(icon._icon_handle, 1234)

    def test_failure_falls_back_to_pystray(self):
        icon = self._fake_icon(crisp_mod.create_icon_image(45, 20, size=16))
        pystray_loader = icon._assert_icon_handle
        tray_pixels_win32.install_exact_icon_handle(icon)

        with patch.object(tray_pixels_win32, '_icon_handle_from_image', side_effect=OSError('refused')), \
             patch.object(tray_pixels_win32.traceback, 'print_exc'):
            icon._assert_icon_handle()

        pystray_loader.assert_called_once_with()

    def test_missing_internals_reported(self):
        """A pystray release without the private attributes leaves the icon alone and reports it."""
        self.assertFalse(tray_pixels_win32.install_exact_icon_handle(object()))


if __name__ == '__main__':
    unittest.main()
