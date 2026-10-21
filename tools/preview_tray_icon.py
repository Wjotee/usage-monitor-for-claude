"""
Tray Icon Preview
==================

Writes enlarged PNG previews of the tray icon for pixel-level inspection
(Windows only).

Each icon is drawn through Windows itself (``DrawIconEx`` onto a taskbar-colored
bitmap at the tray slot size), then enlarged with nearest-neighbour scaling and a
faint pixel grid.  The "before" column runs the original renderer through
pystray's own path (64 px image -> temporary ``.ico`` -> ``LoadImage`` with
``LR_DEFAULTSIZE``); the shell's own downscaler may differ slightly from
``DrawIconEx``, so treat that column as a close approximation.

Usage::

    python tools/preview_tray_icon.py OUTPUT_DIR
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from pystray._util import serialized_image  # type: ignore[import-untyped]  # no type stubs available
from pystray._util import win32 as pystray_win32  # type: ignore[import-untyped]  # no type stubs available

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usage_monitor_for_claude import crisp_tray_icon, tray_icon  # noqa: E402  (needs the path above)
from usage_monitor_for_claude.platforms import tray_pixels_win32  # noqa: E402

SCALINGS = {100: 16, 125: 20, 150: 24, 200: 32}
TASKBARS = {'dark': (32, 32, 32), 'light': (238, 238, 238)}
CELL_PIXELS = 192

# (label, create_icon_image kwargs) - the brief's 7% / 45% / 100% plus the other states
USAGE_LEVELS = {
    '7': dict(pct_top=7, pct_bottom=12, time_pct_top=30, time_pct_bottom=60),
    '45': dict(pct_top=45, pct_bottom=58, time_pct_top=30, time_pct_bottom=70),
    '100': dict(pct_top=100, pct_bottom=40, time_pct_top=80, time_pct_bottom=50),
}
EXTRA_STATES = {
    'idle C': ('icon', 'number+bars', dict(pct_top=0, pct_bottom=0)),
    '$ extra': ('icon', 'number+bars', dict(pct_top=100, pct_bottom=40, extra_usage_available=True)),
    'overage': ('icon', 'number+bars', dict(pct_top=63, pct_bottom=80, mode_top='overage', mode_bottom='overage', time_pct_top=50,
        time_pct_bottom=60)),
    'nums 7/45': ('icon', 'numbers', dict(pct_top=7, pct_bottom=45)),
    'nums 100/62': ('icon', 'numbers', dict(pct_top=100, pct_bottom=62)),
    'nums both 100': ('icon', 'numbers', dict(pct_top=100, pct_bottom=100)),
    'error !': ('status', None, '!'),
    'auth C!': ('status', None, 'C!'),
}

_user32 = ctypes.WinDLL('user32')
_gdi32 = ctypes.WinDLL('gdi32')
_user32.DrawIconEx.argtypes = (
    ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.wintypes.HICON, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.UINT, ctypes.wintypes.HBRUSH, ctypes.wintypes.UINT,
)
_user32.DestroyIcon.argtypes = (ctypes.wintypes.HICON,)
_gdi32.CreateCompatibleDC.argtypes = (ctypes.wintypes.HDC,)
_gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
_gdi32.CreateDIBSection.argtypes = (
    ctypes.wintypes.HDC, ctypes.c_void_p, ctypes.wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
)
_gdi32.CreateDIBSection.restype = ctypes.wintypes.HBITMAP
_gdi32.SelectObject.argtypes = (ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ)
_gdi32.DeleteDC.argtypes = (ctypes.wintypes.HDC,)
_gdi32.DeleteObject.argtypes = (ctypes.wintypes.HGDIOBJ,)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)

    for scaling, size in SCALINGS.items():
        sheet_cells = []
        for taskbar, background in TASKBARS.items():
            light = taskbar == 'light'
            for label, kwargs in USAGE_LEVELS.items():
                with patch.object(crisp_tray_icon, 'ICON_STYLE', 'number+bars'), patch.object(tray_icon, 'ICON_STYLE', 'number+bars'):
                    before = draw_old_path(tray_icon.create_icon_image(light_taskbar=light, **kwargs), size, background)
                    after = draw_exact(crisp_tray_icon.create_icon_image(light_taskbar=light, size=size, **kwargs), background)
                enlarged = enlarge(after, background)
                enlarged.save(output / f'{scaling}pct_{size}px_{taskbar}_{label}pct.png')
                sheet_cells.append((f'{taskbar} {label}%', before, after, background))
        compare_sheet(f'{scaling}% scaling - {size} px tray slot', sheet_cells).save(output / f'compare_{scaling}pct_{size}px.png')

        state_cells = []
        for taskbar, background in TASKBARS.items():
            for label, (kind, style, args) in EXTRA_STATES.items():
                state_cells.append((f'{taskbar} {label}', draw_exact(render_state(kind, style, args, taskbar == 'light', size), background), background))
        states_sheet(f'Other states at {scaling}% ({size} px)', state_cells).save(output / f'states_{scaling}pct_{size}px.png')

    print(f'Wrote previews to {output.resolve()}')


def render_state(kind: str, style: str | None, args, light: bool, size: int) -> Image.Image:
    if kind == 'status':
        return crisp_tray_icon.create_status_image(args, light, size=size)
    with patch.object(crisp_tray_icon, 'ICON_STYLE', style):
        return crisp_tray_icon.create_icon_image(light_taskbar=light, size=size, **args)


def draw_exact(image: Image.Image, background: tuple) -> Image.Image:
    """Draw *image* through the fork's exact-size handle at its own size."""
    handle = tray_pixels_win32._icon_handle_from_image(image)
    try:
        return draw_handle(handle, image.width, background)
    finally:
        _user32.DestroyIcon(handle)


def draw_old_path(image: Image.Image, slot: int, background: tuple) -> Image.Image:
    """Draw *image* the way stock pystray hands it to Windows, shrunk into the tray slot."""
    with serialized_image(image, 'ICO') as icon_path:
        handle = pystray_win32.LoadImage(None, icon_path, pystray_win32.IMAGE_ICON, 0, 0,
            pystray_win32.LR_DEFAULTSIZE | pystray_win32.LR_LOADFROMFILE)
    try:
        return draw_handle(handle, slot, background)
    finally:
        _user32.DestroyIcon(handle)


def draw_handle(handle: int, size: int, background: tuple) -> Image.Image:
    """Return what ``DrawIconEx`` paints for *handle* at *size* onto *background*."""
    header = tray_pixels_win32._BITMAPINFOHEADER(
        biSize=ctypes.sizeof(tray_pixels_win32._BITMAPINFOHEADER), biWidth=size, biHeight=-size, biPlanes=1, biBitCount=32,
    )
    bits = ctypes.c_void_p()
    dc = _gdi32.CreateCompatibleDC(None)
    bitmap = _gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        _gdi32.SelectObject(dc, bitmap)
        fill = bytes((background[2], background[1], background[0], 255)) * (size * size)
        ctypes.memmove(bits.value, fill, len(fill))
        _user32.DrawIconEx(dc, 0, 0, handle, size, size, 0, None, 0x0003)
        return Image.frombytes('RGB', (size, size), ctypes.string_at(bits.value, size * size * 4), 'raw', 'BGRX')
    finally:
        _gdi32.DeleteDC(dc)
        _gdi32.DeleteObject(bitmap)


def enlarge(image: Image.Image, background: tuple) -> Image.Image:
    """Enlarge with nearest-neighbour scaling and draw a faint grid between the pixels."""
    factor = CELL_PIXELS // image.width
    big = image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(big)
    grid = (80, 80, 80) if sum(background) < 384 else (205, 205, 205)
    for i in range(1, image.width):
        draw.line([(i * factor, 0), (i * factor, big.height)], fill=grid)
        draw.line([(0, i * factor), (big.width, i * factor)], fill=grid)
    return big


def compare_sheet(title: str, cells: list) -> Image.Image:
    """Lay out before/after pairs with 1:1 thumbnails."""
    font, small = ImageFont.load_default(18), ImageFont.load_default(14)
    pad, label_h = 16, 24
    width = pad + 2 * (CELL_PIXELS + pad) + 120
    height = 48 + len(cells) * (CELL_PIXELS + label_h + pad)
    sheet = Image.new('RGB', (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 12), title, fill=(0, 0, 0), font=font)
    y = 48
    for label, before, after, background in cells:
        draw.text((pad, y), f'{label}:  before (stock)  |  after (crisp)', fill=(60, 60, 60), font=small)
        y += label_h
        sheet.paste(enlarge(before, background), (pad, y))
        sheet.paste(enlarge(after, background), (pad + CELL_PIXELS + pad, y))
        actual_x = pad + 2 * (CELL_PIXELS + pad)
        draw.text((actual_x, y), 'actual size', fill=(60, 60, 60), font=small)
        for row, image in enumerate((before, after)):
            swatch = Image.new('RGB', (48, 48), background)
            swatch.paste(image, ((48 - image.width) // 2, (48 - image.height) // 2))
            sheet.paste(swatch, (actual_x, y + 24 + row * 60))
        y += CELL_PIXELS + pad
    return sheet


def states_sheet(title: str, cells: list) -> Image.Image:
    """Lay out single renders four per row."""
    font, small = ImageFont.load_default(18), ImageFont.load_default(14)
    pad, label_h, per_row = 16, 22, 4
    rows = (len(cells) + per_row - 1) // per_row
    sheet = Image.new('RGB', (pad + per_row * (CELL_PIXELS + pad), 48 + rows * (CELL_PIXELS + label_h + pad)), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 12), title, fill=(0, 0, 0), font=font)
    for index, (label, image, background) in enumerate(cells):
        x = pad + (index % per_row) * (CELL_PIXELS + pad)
        y = 48 + (index // per_row) * (CELL_PIXELS + label_h + pad)
        draw.text((x, y), label, fill=(60, 60, 60), font=small)
        sheet.paste(enlarge(image, background), (x, y + label_h))
    return sheet


if __name__ == '__main__':
    main()
