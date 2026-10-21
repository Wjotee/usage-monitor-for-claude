"""
Crisp Tray Icon
================

Renders the tray icon at the exact pixel size the notification area draws it
at, so nothing between Pillow and the screen resamples it.  Percentages are
set in the taskbar clock's typeface at the clock's digit height, antialiased
like the clock.  Everything else - bars, marker, the idle 'C', '$', the cross
and the status glyphs - is whole-pixel rectangles in palette colors with hard
edges.  If the clock's typeface is missing, the digits fall back to the same
pixel construction.

Offers the same ``create_icon_image``/``create_status_image`` signatures as
:mod:`usage_monitor_for_claude.tray_icon` and keeps its behavior: both icon
styles, overage and utilization bars, the time marker, the warning fill, the
exhausted glyphs and the status icons.  Where the platform reports no fixed
slot size (a Linux panel scales the icon itself), it hands over to that
module unchanged.

``install_exact_icon_handle`` is re-exported so the app needs a single import
for the whole feature.
"""
from __future__ import annotations

import functools
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont

from . import tray_icon
from .platforms.tray_pixels import install_exact_icon_handle, load_clock_font, tray_icon_size
from .settings import ICON_DARK, ICON_LIGHT, ICON_STYLE

__all__ = ['create_icon_image', 'create_status_image', 'install_exact_icon_handle']

TRANSPARENT = (0, 0, 0, 0)

# DirectWrite draws the taskbar clock heavier than FreeType draws the same
# font: at 100% scaling the clock's digits carry about 1.6 times the ink.  An
# outline stroke of this share of the font size (0.25 px at 11 px) matches it.
CLOCK_STROKE_RATIO = 0.023
CROSS = '✕'

# Glyphs on a 3 x 5 cell grid.  Columns: left stem, counter, right stem.
# Rows: top bar, upper counter, middle bar, lower counter, bottom bar.  The
# cell sizes are worked out per icon size from the stroke widths, so one
# pattern draws every size with whole-pixel strokes.  '1', '$', the cross and
# '!' do not fit the grid and are drawn by their own helpers.
_CELL_GLYPHS: dict[str, tuple[str, str, str, str, str]] = {
    '0': ('###', '#.#', '#.#', '#.#', '###'),
    '2': ('###', '..#', '###', '#..', '###'),
    '3': ('###', '..#', '.##', '..#', '###'),
    '4': ('#.#', '#.#', '###', '..#', '..#'),
    '5': ('###', '#..', '###', '..#', '###'),
    '6': ('###', '#..', '###', '#.#', '###'),
    '7': ('###', '..#', '..#', '..#', '..#'),
    '8': ('###', '#.#', '###', '#.#', '###'),
    '9': ('###', '#.#', '###', '..#', '###'),
    'C': ('###', '#..', '#..', '#..', '###'),
}


class GlyphMetrics(NamedTuple):
    """Pixel metrics shared by every glyph drawn at one height."""

    height: int
    width: int
    stem: int  # vertical stroke width
    bar: int  # horizontal stroke height
    spacing: int  # gap between neighboring glyphs


class IconLayout(NamedTuple):
    """Whole-pixel geometry of one icon size."""

    size: int
    digits: GlyphMetrics  # number above the bars
    digits_top: int  # first pixel row of the number
    rows: GlyphMetrics  # each row of the 'numbers' style
    full: GlyphMetrics  # single glyph filling the icon (status, collapsed rows)
    bar_height: int
    bar_gap: int
    marker_width: int


def create_icon_image(
    pct_top: float, pct_bottom: float, light_taskbar: bool = False,
    *, mode_top: str = 'utilization', mode_bottom: str = 'utilization',
    time_pct_top: float | None = None, time_pct_bottom: float | None = None,
    extra_usage_available: bool = False, size: int | None = None,
) -> Image.Image:
    """Create the tray icon at the notification area's exact pixel size.

    Parameters and behavior match :func:`usage_monitor_for_claude.tray_icon.create_icon_image`.

    Parameters
    ----------
    pct_top, pct_bottom : float
        Utilization percentages (0-100) for the upper and lower bar.
    light_taskbar : bool
        Use dark-on-light colors for a light taskbar.
    mode_top, mode_bottom : str
        ``'utilization'`` (linear fill) or ``'overage'`` (fills as usage
        exceeds the time marker).
    time_pct_top, time_pct_bottom : float or None
        Elapsed-time percentage of each quota window.
    extra_usage_available : bool
        Paid extra usage remains: an exhausted quota shows ``$``, not the cross.
    size : int or None
        Icon edge length in pixels.  Defaults to the platform's tray slot
        size; where the platform has none, the original renderer draws the icon.
    """
    if size is None:
        size = tray_icon_size()
    if size is None:
        return tray_icon.create_icon_image(
            pct_top, pct_bottom, light_taskbar,
            mode_top=mode_top, mode_bottom=mode_bottom,
            time_pct_top=time_pct_top, time_pct_bottom=time_pct_bottom,
            extra_usage_available=extra_usage_available,
        )

    colors = ICON_DARK if light_taskbar else ICON_LIGHT
    layout = icon_layout(size)
    img = Image.new('RGBA', (size, size), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    if ICON_STYLE == 'numbers':
        _draw_numbers_style(draw, layout, pct_top, pct_bottom, extra_usage_available, colors['fg'])
        return img

    any_exhausted = pct_top >= 100 or pct_bottom >= 100
    if any_exhausted and not extra_usage_available:
        text = CROSS
    elif any_exhausted:
        text = '$'
    elif pct_top > 0:
        # Clamp to 99: values in [99.5, 100) would round to a three-digit
        # '100' that does not fit and reads as exhausted.
        text = f'{min(pct_top, 99):.0f}'
    else:
        text = 'C'
    _draw_text(draw, text, layout.digits, size, layout.digits_top, colors['fg'])

    bar2_y = size - layout.bar_height
    bar1_y = bar2_y - layout.bar_gap - layout.bar_height
    _draw_usage_bar(draw, layout, bar1_y, pct_top, mode_top, time_pct_top, colors)
    _draw_usage_bar(draw, layout, bar2_y, pct_bottom, mode_bottom, time_pct_bottom, colors)

    return img


def create_status_image(text: str, light_taskbar: bool = False, *, size: int | None = None) -> Image.Image:
    """Create the monochrome error/status icon (``'!'``, ``'C!'``) at the tray's exact size."""
    if size is None:
        size = tray_icon_size()
    if size is None:
        return tray_icon.create_status_image(text, light_taskbar)

    fg_dim = (ICON_DARK if light_taskbar else ICON_LIGHT)['fg_dim']
    layout = icon_layout(size)
    img = Image.new('RGBA', (size, size), TRANSPARENT)
    _draw_text(ImageDraw.Draw(img), text, layout.full, size, (size - layout.full.height) // 2, fg_dim)

    return img


def icon_layout(size: int) -> IconLayout:
    """Return the whole-pixel geometry for an icon *size* pixels square.

    Proportions follow a 16 px base design and are rounded per size.  The
    number above the bars is half the icon height, the height of the digits
    in the taskbar clock (Segoe UI 9 pt: 8 px at 100% scaling, growing with
    the DPI like the slot does), flush to the top so the spare rows separate
    it from the bars.  For
    example (digit height / bar height / bar gap / marker width):

    ====  =====  ===  ===  ======
    size  digit  bar  gap  marker
    ====  =====  ===  ===  ======
    16    8      2    1    1
    20    10     3    1    1
    24    12     3    1    2
    32    16     4    2    2
    ====  =====  ===  ===  ======
    """
    if size < 16:
        raise ValueError(f'Tray icon size must be at least 16 px, got {size}')

    unit = size / 16
    bar_height = int(2 * unit + 0.5)
    bar_gap = int(unit)
    spacing = max(1, round(unit))
    digit_height = round(size / 2)
    above_bars = size - 2 * bar_height - bar_gap
    row_height = (size - 2 * bar_gap) // 2
    full_height = size - 2 * max(2, size // 8)
    max_width = (size - spacing) // 2

    return IconLayout(
        size=size,
        # Clock-like proportions: about three quarters as wide as tall, with 2 px counters
        digits=_glyph_metrics(digit_height, max_width, spacing, size, width_ratio=0.75, min_counter=2),
        digits_top=0,  # flush to the top, leaving all spare rows as the gap above the bars
        rows=_glyph_metrics(row_height, max_width, spacing, size),
        full=_glyph_metrics(full_height, max_width, spacing, size),
        bar_height=bar_height,
        bar_gap=bar_gap,
        marker_width=max(1, round(unit)),
    )


def _glyph_metrics(height: int, max_width: int, spacing: int, icon_size: int, *, width_ratio: float = 0.65,
        min_counter: int = 3) -> GlyphMetrics:
    """Derive stroke widths and glyph width from a glyph *height*.

    Horizontal bars are about a fifth of the height so the two counters stay
    open; vertical stems are at least 2 px, the narrowest stroke that still
    reads as bold on a taskbar.  Width is *width_ratio* of the height but
    leaves the counters at least *min_counter* pixels wide.  Spacing grows up
    to double where two glyphs would otherwise leave spare columns, so a
    two-digit number sits evenly in the icon.
    """
    bar = max(1, round(height / 5.5))
    stem = max(2, bar)
    width = min(max_width, max(round(height * width_ratio), 2 * stem + min_counter))
    if width < 2 * stem + 1 or height < 3 * bar + 2:
        raise ValueError(f'Glyph height {height} leaves no room for its strokes')

    spacing = max(spacing, min(2 * spacing, icon_size - 2 * width))

    return GlyphMetrics(height=height, width=width, stem=stem, bar=bar, spacing=spacing)


def _draw_numbers_style(draw: ImageDraw.ImageDraw, layout: IconLayout, pct_top: float, pct_bottom: float, extra_usage_available: bool,
        fg: tuple) -> None:
    """Draw the ``'numbers'`` style: two stacked percentages, or one glyph for the shared states."""
    size = layout.size
    full_top = (size - layout.full.height) // 2

    # Two states collapse both rows into one full-size glyph: idle shows the
    # single 'C', and both quotas exhausted shows one cross or '$' - extra
    # usage applies account-wide, so two rows would only repeat the symbol.
    if pct_top >= 100 and pct_bottom >= 100:
        _draw_text(draw, '$' if extra_usage_available else CROSS, layout.full, size, full_top, fg)
        return
    if pct_top <= 0 and pct_bottom <= 0:
        _draw_text(draw, 'C', layout.full, size, full_top, fg)
        return

    _draw_text(draw, _row_text(pct_top, extra_usage_available), layout.rows, size, 0, fg)
    _draw_text(draw, _row_text(pct_bottom, extra_usage_available), layout.rows, size, size - layout.rows.height, fg)


def _row_text(pct: float, extra_usage_available: bool) -> str:
    """Return the text of one ``'numbers'`` row, clamped to 99 below exhaustion."""
    if pct >= 100:
        return '$' if extra_usage_available else CROSS

    return f'{min(pct, 99):.0f}'


def _draw_usage_bar(draw: ImageDraw.ImageDraw, layout: IconLayout, y: int, pct: float, mode: str, time_pct: float | None,
        colors: dict) -> None:
    """Draw one full-width usage bar with its top edge at *y*.

    Same rules as :func:`usage_monitor_for_claude.tray_icon._draw_usage_bar`,
    on whole pixels: in ``utilization`` mode the fill grows with *pct*, turns
    ``fg_warn`` when usage is ahead of *time_pct* or exhausted, and a marker
    in ``fg`` shows the elapsed time.  In ``overage`` mode the fill grows as
    *pct* exceeds *time_pct* and no marker is drawn.
    """
    size = layout.size
    bottom = y + layout.bar_height
    _fill(draw, 0, y, size, bottom, colors['fg_half'])

    if mode == 'overage' and time_pct is not None:
        if time_pct >= 100:
            # Stale window (elapsed time clamped to 100%, e.g. between a reset
            # and the confirming poll): usage below the limit stayed within
            # budget (empty bar), an exhausted quota keeps the bar full.
            if pct >= 100:
                _fill(draw, 0, y, size, bottom, colors['fg'])
            return

        overage = max(0.0, pct - time_pct)
        fill_ratio = min(1.0, overage / (100 - time_pct))
        _fill(draw, 0, y, int(size * fill_ratio), bottom, colors['fg'])
        return

    fill_width = max(0, min(size, int(size * pct / 100)))
    warn = mode == 'utilization' and (pct >= 100 or (time_pct is not None and pct > time_pct))
    _fill(draw, 0, y, fill_width, bottom, colors['fg_warn'] if warn else colors['fg'])

    if mode != 'utilization' or time_pct is None:
        return

    marker_width = layout.marker_width
    marker_x = min(size - marker_width, max(0, int(size * time_pct / 100) - marker_width // 2))
    _fill(draw, marker_x, y, marker_x + marker_width, bottom, colors['fg'])


def _draw_text(draw: ImageDraw.ImageDraw, text: str, metrics: GlyphMetrics, icon_size: int, top: int, fill: tuple) -> None:
    """Draw *text* horizontally centered in the icon within the *metrics.height* rows starting at *top*."""
    clock_font = _clock_digit_font(metrics.height) if text.isdigit() else None
    if clock_font is not None:
        _draw_clock_digits(draw, text, clock_font, metrics.height, icon_size, top, fill)
        return

    advances = [_glyph_width(char, metrics, icon_size) for char in text]
    x = (icon_size - sum(advances) - metrics.spacing * (len(text) - 1)) // 2
    for char, advance in zip(text, advances):
        _draw_glyph(draw, char, x, top, metrics, advance, fill)
        x += advance + metrics.spacing


@functools.lru_cache(maxsize=None)
def _clock_digit_font(height: int) -> ImageFont.FreeTypeFont | None:
    """Return the clock's typeface at the largest size whose digits fit *height* rows, or None if it is unavailable.

    The size is chosen by the inked digit height rather than by point size:
    the clock's DirectWrite renderer snaps only vertically and draws 8 px
    digits at its 12 px, while FreeType hints the same size to 9 px.  Sizes
    are tried in half-pixel steps downward.
    """
    pixel_size = 2.0 * height
    while pixel_size >= 6:
        font = load_clock_font(pixel_size)
        if font is None:
            return None
        _left, ink_top, _right, ink_bottom = _ink_box(font, '0123456789', 0)
        if ink_bottom - ink_top <= height:
            return font
        pixel_size -= 0.5

    return None


def _draw_clock_digits(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, height: int, icon_size: int, top: int,
        fill: tuple) -> None:
    """Draw digits in the clock's typeface, centered horizontally and in the *height* rows starting at *top*.

    The origin stays on whole pixels so the font's hinting lines stems up with
    the pixel grid.  Centering uses the pixels the rasterizer actually inks -
    hinting shifts them away from the font's metric box by up to two pixels.
    The vertical position comes from the ink of all ten digits, so every
    number sits on the same baseline.  The emboldening stroke leaves a faint
    fringe under rounded digits; it is clipped, because the clock's own
    digits stay exactly *height* rows tall.
    """
    stroke = font.size * CLOCK_STROKE_RATIO
    text_left, _text_top, text_right, _text_bottom = _ink_box(font, text, stroke)
    _digits_left, digits_top, _digits_right, digits_bottom = _ink_box(font, '0123456789', 0)
    x = (icon_size - (text_right - text_left)) // 2 - text_left
    y = top + (height - (digits_bottom - digits_top)) // 2 - digits_top

    coverage = Image.new('L', (icon_size, icon_size), 0)
    ImageDraw.Draw(coverage).text((x, y), text, fill=255, font=font, stroke_width=stroke, stroke_fill=255)
    clipped = Image.new('L', (icon_size, icon_size), 0)
    clipped.paste(coverage.crop((0, top, icon_size, top + height)), (0, top))
    draw.bitmap((0, 0), clipped, fill=fill)


@functools.lru_cache(maxsize=None)
def _ink_box(font: ImageFont.FreeTypeFont, text: str, stroke: float) -> tuple[int, int, int, int]:
    """Return the (left, top, right, bottom) box of the pixels *text* inks when drawn at the origin with *stroke*."""
    _left, _top, right, bottom = font.getbbox(text, stroke_width=stroke)
    margin = 4
    canvas = Image.new('L', (int(right) + 2 * margin, int(bottom) + 2 * margin), 0)
    ImageDraw.Draw(canvas).text((margin, margin), text, fill=255, font=font, stroke_width=stroke, stroke_fill=255)
    ink_left, ink_top, ink_right, ink_bottom = canvas.getbbox() or (margin, margin, margin, margin)

    return ink_left - margin, ink_top - margin, ink_right - margin, ink_bottom - margin


def _glyph_width(char: str, metrics: GlyphMetrics, icon_size: int) -> int:
    """Return the horizontal advance of *char*."""
    if char == CROSS:
        return min(metrics.height, icon_size)
    if char == '!':
        return metrics.stem

    return metrics.width


def _draw_glyph(draw: ImageDraw.ImageDraw, char: str, x: int, y: int, metrics: GlyphMetrics, width: int, fill: tuple) -> None:
    """Draw one glyph with its top-left corner at (*x*, *y*)."""
    if char in _CELL_GLYPHS:
        _draw_cells(draw, _CELL_GLYPHS[char], x, y, width, metrics.height, metrics, fill)
    elif char == '1':
        _draw_one(draw, x, y, metrics, fill)
    elif char == '$':
        _draw_dollar(draw, x, y, metrics, fill)
    elif char == CROSS:
        _draw_cross(draw, x, y, width, metrics.stem, fill)
    elif char == '!':
        _draw_exclamation(draw, x, y, metrics, fill)
    else:
        raise ValueError(f'No tray glyph for {char!r}')


def _draw_cells(draw: ImageDraw.ImageDraw, pattern: tuple[str, ...], x: int, y: int, width: int, height: int, metrics: GlyphMetrics,
        fill: tuple) -> None:
    """Fill the '#' cells of a 3 x 5 *pattern* inside the given box."""
    column_edges = (x, x + metrics.stem, x + width - metrics.stem, x + width)
    row_edges = _row_edges(y, height, metrics.bar)
    for row_index, row in enumerate(pattern):
        for column_index, cell in enumerate(row):
            if cell == '#':
                _fill(draw, column_edges[column_index], row_edges[row_index], column_edges[column_index + 1], row_edges[row_index + 1], fill)


def _row_edges(y: int, height: int, bar: int) -> tuple[int, int, int, int, int, int]:
    """Return the six y edges of the five cell rows; the lower counter takes any odd pixel."""
    upper_counter = (height - 3 * bar) // 2
    middle = y + bar + upper_counter

    return (y, y + bar, middle, middle + bar, y + height - bar, y + height)


def _draw_one(draw: ImageDraw.ImageDraw, x: int, y: int, metrics: GlyphMetrics, fill: tuple) -> None:
    """Draw '1': a full-height stem just right of center with a flag to its left."""
    stem_x = x + (metrics.width - metrics.stem + 1) // 2
    flag_top = y + max(1, metrics.bar // 2)
    _fill(draw, stem_x, y, stem_x + metrics.stem, y + metrics.height, fill)
    _fill(draw, max(x, stem_x - metrics.stem), flag_top, stem_x, flag_top + metrics.bar, fill)


def _draw_dollar(draw: ImageDraw.ImageDraw, x: int, y: int, metrics: GlyphMetrics, fill: tuple) -> None:
    """Draw '$': the '5' shape shortened at both ends, with the stem showing above and below."""
    stem_length = max(1, round(metrics.height / 10))
    body_height = metrics.height - 2 * stem_length
    _draw_cells(draw, _CELL_GLYPHS['5'], x, y + stem_length, metrics.width, body_height, metrics, fill)
    _fill(draw, x + metrics.stem, y, x + metrics.width - metrics.stem, y + stem_length, fill)
    _fill(draw, x + metrics.stem, y + metrics.height - stem_length, x + metrics.width - metrics.stem, y + metrics.height, fill)


def _draw_cross(draw: ImageDraw.ImageDraw, x: int, y: int, side: int, stroke: int, fill: tuple) -> None:
    """Draw the cross as two stepped diagonals, *stroke* pixels wide in every row."""
    travel = side - stroke
    for row in range(side):
        offset = round(row * travel / (side - 1))
        _fill(draw, x + offset, y + row, x + offset + stroke, y + row + 1, fill)
        _fill(draw, x + travel - offset, y + row, x + travel - offset + stroke, y + row + 1, fill)


def _draw_exclamation(draw: ImageDraw.ImageDraw, x: int, y: int, metrics: GlyphMetrics, fill: tuple) -> None:
    """Draw '!': a stem and a square dot separated by a gap."""
    dot = metrics.stem
    gap = max(1, round(metrics.height / 8))
    _fill(draw, x, y, x + metrics.stem, y + metrics.height - dot - gap, fill)
    _fill(draw, x, y + metrics.height - dot, x + metrics.stem, y + metrics.height, fill)


def _fill(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int, color: tuple) -> None:
    """Fill the pixels in [*left*, *right*) x [*top*, *bottom*); an empty range draws nothing."""
    if right > left and bottom > top:
        draw.rectangle([left, top, right - 1, bottom - 1], fill=color)
