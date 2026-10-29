"""
Crisp Tray Icon Tests
======================

Unit tests for the exact-size tray icon renderer.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import usage_monitor_for_claude.crisp_tray_icon as crisp_mod
import usage_monitor_for_claude.tray_icon as tray_icon_mod

# 100%, 125%, 150%, 175%, 200%, 250% and 300% display scaling
TRAY_SIZES = (16, 20, 24, 28, 32, 40, 48)
TRANSPARENT = (0, 0, 0, 0)


def setUpModule():
    # Pin the default icon style so a local usage-monitor-settings.json with
    # 'icon_style' cannot flip the classic-style rendering tests.
    patcher = patch.object(crisp_mod, 'ICON_STYLE', 'number+bars')
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _bar_rows(size):
    """Return the y ranges of the upper and lower bar at *size*."""
    layout = crisp_mod.icon_layout(size)
    bar2_y = size - layout.bar_height
    bar1_y = bar2_y - layout.bar_gap - layout.bar_height
    return range(bar1_y, bar1_y + layout.bar_height), range(bar2_y, size)


def _row_colors(img, y):
    """Return the pixel colors of row *y* from left to right."""
    return [img.getpixel((x, y)) for x in range(img.width)]


def _colors(img):
    """Return the set of distinct pixel colors in *img*."""
    return {color for _count, color in img.getcolors(img.width * img.height)}


def _ink_rows(img, y_range=None):
    """Return the set of rows (within *y_range*) holding any non-transparent pixel."""
    rows = y_range if y_range is not None else range(img.height)
    return {y for y in rows for x in range(img.width) if img.getpixel((x, y))[3] > 0}


def _digit_rows(size):
    """Return the y range of the classic-style number at *size*."""
    layout = crisp_mod.icon_layout(size)
    return range(layout.digits_top, layout.digits_top + layout.digits.height)


def _without_clock_font():
    """Patch the clock typeface away so the pixel-digit fallback draws the numbers; returns the started patcher."""
    crisp_mod._clock_digit_font.cache_clear()
    patcher = patch.object(crisp_mod, 'load_clock_font', return_value=None)
    patcher.start()
    return patcher


def _rows_with_color(img, color):
    """Return the set of rows that contain *color*."""
    return {y for y in range(img.height) for x in range(img.width) if img.getpixel((x, y)) == color}


class TestExactSize(unittest.TestCase):
    """The icon is drawn at the requested slot size, never at a fixed canvas."""

    def test_icon_matches_every_tray_size(self):
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                img = crisp_mod.create_icon_image(45, 20, size=size)
                self.assertEqual(img.size, (size, size))
                self.assertEqual(img.mode, 'RGBA')

    def test_status_matches_every_tray_size(self):
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                self.assertEqual(crisp_mod.create_status_image('C!', size=size).size, (size, size))

    def test_default_size_comes_from_platform(self):
        """Without an explicit size the platform's tray slot size is used."""
        with patch.object(crisp_mod, 'tray_icon_size', return_value=20):
            self.assertEqual(crisp_mod.create_icon_image(45, 20).size, (20, 20))
            self.assertEqual(crisp_mod.create_status_image('!').size, (20, 20))

    def test_falls_back_to_original_renderer_without_slot_size(self):
        """A platform without a fixed slot size (Linux) gets the original 64 px icon, unchanged."""
        with patch.object(crisp_mod, 'tray_icon_size', return_value=None), \
             patch.object(tray_icon_mod, 'ICON_STYLE', 'number+bars'):
            img = crisp_mod.create_icon_image(45, 20, light_taskbar=True, time_pct_top=30, extra_usage_available=True)
            reference = tray_icon_mod.create_icon_image(45, 20, light_taskbar=True, time_pct_top=30, extra_usage_available=True)
            status = crisp_mod.create_status_image('!', light_taskbar=True)
            status_reference = tray_icon_mod.create_status_image('!', light_taskbar=True)

        self.assertEqual(img.tobytes(), reference.tobytes())
        self.assertEqual(status.tobytes(), status_reference.tobytes())

    def test_sizes_below_16_rejected(self):
        with self.assertRaises(ValueError):
            crisp_mod.icon_layout(15)


class TestPixelColors(unittest.TestCase):
    """Bars and symbols use exact palette colors; digits only vary in alpha, never in color."""

    def _assert_palette_only(self, img, allowed, y_range=None):
        rows = y_range if y_range is not None else range(img.height)
        stray = {img.getpixel((x, y)) for y in rows for x in range(img.width)} - allowed
        self.assertFalse(stray, f'Blended pixel colors found: {sorted(stray)[:5]}')

    def _assert_foreground_rgb(self, img, fg, y_range):
        """Antialiased pixels keep the foreground color and fade only through alpha - no dark fringe."""
        stray = {img.getpixel((x, y)) for y in y_range for x in range(img.width)
                 if img.getpixel((x, y))[3] > 0 and img.getpixel((x, y))[:3] != fg[:3]}
        self.assertFalse(stray, f'Off-color text pixels found: {sorted(stray)[:5]}')

    def test_bars_use_palette_colors_only(self):
        for light in (False, True):
            colors = crisp_mod.ICON_DARK if light else crisp_mod.ICON_LIGHT
            allowed = {TRANSPARENT, colors['fg'], colors['fg_half'], colors['fg_warn']}
            for size in TRAY_SIZES:
                for pct_top, pct_bottom in ((7, 45), (45, 60), (100, 30)):
                    with self.subTest(light=light, size=size, pct_top=pct_top, pct_bottom=pct_bottom):
                        img = crisp_mod.create_icon_image(pct_top, pct_bottom, light, time_pct_top=30, time_pct_bottom=50, size=size)
                        self._assert_palette_only(img, allowed, range(_digit_rows(size).stop, size))

    def test_symbols_keep_the_foreground_color(self):
        """The cross and '$' are font glyphs: they fade only through alpha."""
        for light in (False, True):
            fg = (crisp_mod.ICON_DARK if light else crisp_mod.ICON_LIGHT)['fg']
            for size in TRAY_SIZES:
                for extra in (False, True):
                    with self.subTest(light=light, size=size, extra=extra):
                        img = crisp_mod.create_icon_image(100, 30, light, extra_usage_available=extra, size=size)
                        self._assert_foreground_rgb(img, fg, _digit_rows(size))

    def test_digits_keep_the_foreground_color(self):
        for light in (False, True):
            fg = (crisp_mod.ICON_DARK if light else crisp_mod.ICON_LIGHT)['fg']
            for size in TRAY_SIZES:
                for pct_top in (0, 7, 45, 88):
                    with self.subTest(light=light, size=size, pct_top=pct_top):
                        img = crisp_mod.create_icon_image(pct_top, 30, light, size=size)
                        self._assert_foreground_rgb(img, fg, _digit_rows(size))

    def test_numbers_style_keeps_the_foreground_color(self):
        fg = crisp_mod.ICON_LIGHT['fg']
        with patch.object(crisp_mod, 'ICON_STYLE', 'numbers'):
            for size in TRAY_SIZES:
                for pct_top, pct_bottom in ((0, 0), (7, 45), (100, 62), (100, 100), (19, 83)):
                    with self.subTest(size=size, pct_top=pct_top, pct_bottom=pct_bottom):
                        img = crisp_mod.create_icon_image(pct_top, pct_bottom, size=size)
                        self._assert_foreground_rgb(img, fg, range(size))

    def test_status_uses_dim_color_only(self):
        for size in TRAY_SIZES:
            for text in ('!', 'C!'):
                with self.subTest(size=size, text=text):
                    img = crisp_mod.create_status_image(text, size=size)
                    self._assert_foreground_rgb(img, crisp_mod.ICON_LIGHT['fg_dim'], range(size))

    def test_pixel_digit_fallback_uses_palette_colors_only(self):
        """Without the clock's typeface the digits fall back to whole-pixel shapes."""
        patcher = _without_clock_font()
        self.addCleanup(crisp_mod._clock_digit_font.cache_clear)
        self.addCleanup(patcher.stop)
        colors = crisp_mod.ICON_LIGHT
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                img = crisp_mod.create_icon_image(45, 60, time_pct_top=30, time_pct_bottom=50, size=size)
                self._assert_palette_only(img, {TRANSPARENT, colors['fg'], colors['fg_half'], colors['fg_warn']})
                self.assertTrue(_ink_rows(img, _digit_rows(size)))


@unittest.skipUnless(sys.platform == 'win32', 'The clock typeface is only loaded on Windows')
class TestClockFont(unittest.TestCase):
    """Digits are set in the taskbar clock's typeface at the clock's digit height."""

    def setUp(self):
        crisp_mod._clock_digit_font.cache_clear()

    def test_largest_size_that_fits(self):
        """The chosen size inks digits within the height; half a pixel more would not fit."""
        for size in TRAY_SIZES:
            height = crisp_mod.icon_layout(size).digits.height
            with self.subTest(size=size):
                font = crisp_mod._clock_digit_font(height)
                self.assertIsNotNone(font)
                self.assertTrue(font.path.lower().endswith('segoeui.ttf'))
                _left, top, _right, bottom = font.getbbox('0123456789')
                self.assertLessEqual(bottom - top, height)
                _left, top, _right, bottom = crisp_mod.load_clock_font(font.size + 0.5).getbbox('0123456789')
                self.assertGreater(bottom - top, height)

    def test_16px_uses_11px_segoe_ui(self):
        """At 100% scaling FreeType needs 11 px Segoe UI for the clock's 8 px digits."""
        self.assertEqual(crisp_mod._clock_digit_font(8).size, 11)

    def test_digits_are_antialiased_like_the_clock(self):
        img = crisp_mod.create_icon_image(45, 30, size=16)
        alphas = {img.getpixel((x, y))[3] for y in _digit_rows(16) for x in range(16)}

        self.assertTrue(any(0 < alpha < 255 for alpha in alphas))

    def test_digits_differ_from_pixel_fallback(self):
        with_font = crisp_mod.create_icon_image(45, 30, size=16)
        patcher = _without_clock_font()
        try:
            fallback = crisp_mod.create_icon_image(45, 30, size=16)
        finally:
            patcher.stop()
            crisp_mod._clock_digit_font.cache_clear()

        self.assertNotEqual(with_font.tobytes(), fallback.tobytes())


class TestLayout(unittest.TestCase):
    """Whole-pixel geometry per size."""

    def test_16px_geometry(self):
        """At 100% scaling: 8 x 6 px digits on rows 0-7, a 3 px gap, two 2 px bars with a 1 px gap, 1 px marker."""
        layout = crisp_mod.icon_layout(16)

        self.assertEqual((layout.digits.height, layout.digits.width, layout.digits_top), (8, 6, 0))
        self.assertEqual((layout.bar_height, layout.bar_gap, layout.marker_width), (2, 1, 1))
        self.assertEqual(layout.rows.height, 7)

    def test_32px_is_16px_doubled(self):
        """200% scaling keeps the 100% proportions at twice the pixels."""
        small, large = crisp_mod.icon_layout(16), crisp_mod.icon_layout(32)

        self.assertEqual(large.digits.height, 2 * small.digits.height)
        self.assertEqual(large.bar_height, 2 * small.bar_height)
        self.assertEqual(large.bar_gap, 2 * small.bar_gap)

    def test_digits_match_the_taskbar_clock(self):
        """Digits are half the icon height - the taskbar clock's digit height at every scaling (8 px at 100%)."""
        for size, expected in ((16, 8), (20, 10), (24, 12), (32, 16)):
            with self.subTest(size=size):
                self.assertEqual(crisp_mod.icon_layout(size).digits.height, expected)

    def test_digits_flush_top_with_gap_above_the_bars(self):
        """The number starts at the top row; every spare row separates it from the bars."""
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                layout = crisp_mod.icon_layout(size)
                upper_bar, _lower_bar = _bar_rows(size)
                self.assertEqual(layout.digits_top, 0)
                self.assertGreaterEqual(upper_bar.start - layout.digits.height, 2)

    def test_two_digits_fit_the_width(self):
        for size in TRAY_SIZES:
            layout = crisp_mod.icon_layout(size)
            for metrics in (layout.digits, layout.rows):
                with self.subTest(size=size, metrics=metrics):
                    self.assertLessEqual(2 * metrics.width + metrics.spacing, size)

    def test_digits_do_not_touch_the_bars(self):
        """The rows above the digits and between the digits and the upper bar stay transparent."""
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                img = crisp_mod.create_icon_image(88, 20, size=size)
                upper_bar, _lower_bar = _bar_rows(size)
                layout = crisp_mod.icon_layout(size)
                for y in [*range(layout.digits_top), *range(layout.digits_top + layout.digits.height, upper_bar.start)]:
                    self.assertTrue(all(color == TRANSPARENT for color in _row_colors(img, y)), f'Row {y} is not empty')

    def test_digits_fill_their_box(self):
        """'88' inks inside the digit rows and spans all of them but at most one."""
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                img = crisp_mod.create_icon_image(88, 20, size=size)
                box = _digit_rows(size)
                rows = _ink_rows(img, range(_bar_rows(size)[0].start))
                self.assertTrue(rows <= set(box), f'Ink rows {sorted(rows)} outside {box}')
                self.assertGreaterEqual(len(rows), len(box) - 1)

    def test_all_digits_distinct(self):
        """Each digit renders a different bitmap at every size and in both styles."""
        for size in TRAY_SIZES:
            layout = crisp_mod.icon_layout(size)
            for metrics in (layout.digits, layout.rows):
                bitmaps = set()
                for digit in '0123456789':
                    img = Image.new('RGBA', (size, size), TRANSPARENT)
                    crisp_mod._draw_text(ImageDraw.Draw(img), digit, metrics, size, 0, (255, 255, 255, 255))
                    bitmaps.add(img.tobytes())
                with self.subTest(size=size, height=metrics.height):
                    self.assertEqual(len(bitmaps), 10)

    def test_unknown_glyph_rejected(self):
        img = Image.new('RGBA', (16, 16), TRANSPARENT)
        with self.assertRaises(ValueError):
            crisp_mod._draw_text(ImageDraw.Draw(img), '%', crisp_mod.icon_layout(16).digits, 16, 0, (255, 255, 255, 255))


class TestBars(unittest.TestCase):
    """Bar edges land on whole pixels and keep the original bar semantics."""

    _FG = crisp_mod.ICON_LIGHT['fg']
    _HALF = crisp_mod.ICON_LIGHT['fg_half']
    _WARN = crisp_mod.ICON_LIGHT['fg_warn']

    def test_bar_rows_are_uniform(self):
        """Every row of a bar is identical - no half-blended top or bottom row."""
        for size in TRAY_SIZES:
            img = crisp_mod.create_icon_image(45, 60, time_pct_top=30, time_pct_bottom=70, size=size)
            for bar in _bar_rows(size):
                with self.subTest(size=size, bar=bar):
                    first = _row_colors(img, bar.start)
                    for y in bar:
                        self.assertEqual(_row_colors(img, y), first)

    def test_16px_bar_positions(self):
        """At 16 px the bars occupy rows 11-12 and 14-15 with rows 10 and 13 empty."""
        img = crisp_mod.create_icon_image(45, 60, size=16)

        self.assertEqual(_bar_rows(16), (range(11, 13), range(14, 16)))
        for y in (10, 13):
            self.assertTrue(all(color == TRANSPARENT for color in _row_colors(img, y)))

    def test_fill_widths_at_16px(self):
        """Fill width is the whole-pixel floor of the percentage of the icon width."""
        for pct, expected in ((7, 1), (45, 7), (50, 8), (99, 15), (100, 16), (5, 0)):
            with self.subTest(pct=pct):
                img = crisp_mod.create_icon_image(20, pct, size=16)
                row = _row_colors(img, 15)
                filled = sum(1 for color in row if color in (self._FG, self._WARN))
                self.assertEqual(filled, expected)
                self.assertEqual(row[expected:], [self._HALF] * (16 - expected))

    def test_marker_single_pixel_at_16px(self):
        """The time marker is one whole column in fg on the track."""
        img = crisp_mod.create_icon_image(10, 10, time_pct_top=50, time_pct_bottom=50, size=16)

        row = _row_colors(img, 15)
        self.assertEqual(row[8], self._FG)
        self.assertEqual(row[7], self._HALF)
        self.assertEqual(row[9], self._HALF)

    def test_marker_two_pixels_at_32px(self):
        img = crisp_mod.create_icon_image(10, 10, time_pct_top=50, time_pct_bottom=50, size=32)

        row = _row_colors(img, 31)
        self.assertEqual(row[15:17], [self._FG, self._FG])
        self.assertEqual(row[14], self._HALF)
        self.assertEqual(row[17], self._HALF)

    def test_marker_clamped_inside_edges(self):
        for size in TRAY_SIZES:
            for time_pct, x in ((0, 0), (100, size - 1)):
                with self.subTest(size=size, time_pct=time_pct):
                    img = crisp_mod.create_icon_image(0.0001, 0.0001, time_pct_top=time_pct, time_pct_bottom=time_pct, size=size)
                    self.assertEqual(img.getpixel((x, size - 1)), self._FG)

    def test_no_marker_without_time_pct(self):
        img = crisp_mod.create_icon_image(20, 20, size=16)

        self.assertEqual(_row_colors(img, 15)[3:], [self._HALF] * 13)

    def test_fill_warns_when_ahead_of_time(self):
        img = crisp_mod.create_icon_image(70, 70, time_pct_top=40, time_pct_bottom=40, size=16)

        row = _row_colors(img, 15)
        self.assertEqual(row[0], self._WARN)
        self.assertEqual(row[6], self._FG)  # marker at int(16 * 0.4) = 6
        self.assertEqual(row[10], self._WARN)
        self.assertEqual(row[11], self._HALF)

    def test_fill_plain_when_on_pace(self):
        img = crisp_mod.create_icon_image(50, 50, time_pct_top=50, time_pct_bottom=50, size=16)

        self.assertEqual(_row_colors(img, 15)[:8], [self._FG] * 8)

    def test_fill_warns_at_full_usage_without_time_pct(self):
        img = crisp_mod.create_icon_image(100, 100, size=16)

        self.assertEqual(_row_colors(img, 15), [self._WARN] * 16)

    def test_overage_half_fill(self):
        """pct halfway between time_pct and 100% fills half the bar, in fg, without a marker."""
        img = crisp_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60, size=16)

        self.assertEqual(_row_colors(img, 15), [self._FG] * 8 + [self._HALF] * 8)

    def test_overage_on_pace_is_empty(self):
        img = crisp_mod.create_icon_image(40, 40, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60, size=16)

        self.assertEqual(_row_colors(img, 15), [self._HALF] * 16)

    def test_overage_stale_window(self):
        """With elapsed time clamped to 100%, usage below the limit stays empty and an exhausted quota stays full."""
        below = crisp_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100, size=16)
        exhausted = crisp_mod.create_icon_image(100, 100, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100,
            size=16)

        self.assertEqual(_row_colors(below, 15), [self._HALF] * 16)
        self.assertEqual(_row_colors(exhausted, 15), [self._FG] * 16)

    def test_mixed_modes(self):
        """Top bar in overage mode, bottom bar in utilization mode."""
        img = crisp_mod.create_icon_image(80, 50, mode_top='overage', mode_bottom='utilization', time_pct_top=60, size=16)

        self.assertEqual(_row_colors(img, 12), [self._FG] * 8 + [self._HALF] * 8)
        self.assertEqual(_row_colors(img, 15), [self._FG] * 8 + [self._HALF] * 8)

    def test_light_taskbar_palette(self):
        dark = crisp_mod.ICON_DARK
        img = crisp_mod.create_icon_image(100, 100, light_taskbar=True, time_pct_top=50, time_pct_bottom=50, size=16)

        row = _row_colors(img, 15)
        self.assertEqual(row[0], dark['fg_warn'])
        self.assertEqual(row[8], dark['fg'])


class TestClassicGlyph(unittest.TestCase):
    """The glyph above the bars follows the original rules."""

    def _glyph_area(self, img, size):
        return img.crop((0, 0, size, _bar_rows(size)[0].start)).tobytes()

    def test_zero_usage_shows_c_not_zero(self):
        img_idle = crisp_mod.create_icon_image(0, 0, size=16)
        img_fraction = crisp_mod.create_icon_image(0.3, 0, size=16)

        self.assertNotEqual(self._glyph_area(img_idle, 16), self._glyph_area(img_fraction, 16))

    def test_99_5_renders_like_99(self):
        reference = crisp_mod.create_icon_image(99.0, 10.0, size=16)
        for pct in (99.5, 99.9, 99.99):
            with self.subTest(pct=pct):
                self.assertEqual(crisp_mod.create_icon_image(pct, 10.0, size=16).tobytes(), reference.tobytes())

    def test_exhausted_cross_vs_dollar(self):
        cross = crisp_mod.create_icon_image(100, 20, size=16)
        dollar = crisp_mod.create_icon_image(100, 20, extra_usage_available=True, size=16)
        number = crisp_mod.create_icon_image(99, 20, size=16)

        self.assertNotEqual(self._glyph_area(cross, 16), self._glyph_area(dollar, 16))
        self.assertNotEqual(self._glyph_area(cross, 16), self._glyph_area(number, 16))

    def test_bottom_exhaustion_also_shows_cross(self):
        top_exhausted = crisp_mod.create_icon_image(100, 20, size=16)
        bottom_exhausted = crisp_mod.create_icon_image(20, 100, size=16)

        self.assertEqual(self._glyph_area(top_exhausted, 16), self._glyph_area(bottom_exhausted, 16))

    def test_extra_usage_irrelevant_below_exhaustion(self):
        plain = crisp_mod.create_icon_image(75, 20, size=16)
        with_extra = crisp_mod.create_icon_image(75, 20, extra_usage_available=True, size=16)

        self.assertEqual(plain.tobytes(), with_extra.tobytes())

    def test_glyph_centered(self):
        """Single glyphs sit centered: equal empty margins left and right, within one pixel."""
        for size in TRAY_SIZES:
            for pct_top in (0, 7, 100):
                with self.subTest(size=size, pct_top=pct_top):
                    img = crisp_mod.create_icon_image(pct_top, 20, size=size)
                    columns = [x for x in range(size) for y in range(_bar_rows(size)[0].start)
                               if img.getpixel((x, y)) != TRANSPARENT]
                    left, right = min(columns), size - 1 - max(columns)
                    self.assertLessEqual(abs(left - right), 1)

    def test_dark_and_light_differ(self):
        self.assertNotEqual(crisp_mod.create_icon_image(50, 50, size=16).tobytes(),
                            crisp_mod.create_icon_image(50, 50, light_taskbar=True, size=16).tobytes())


class TestNumbersStyle(unittest.TestCase):
    """The 'numbers' style: two stacked percentages, no bars, always fg."""

    def setUp(self):
        patcher = patch.object(crisp_mod, 'ICON_STYLE', 'numbers')
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _rows(size):
        height = crisp_mod.icon_layout(size).rows.height
        return range(0, height), range(size - height, size)

    def test_rows_separated_by_empty_lines(self):
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                img = crisp_mod.create_icon_image(88, 88, size=size)
                top, bottom = self._rows(size)
                for y in range(top.stop, bottom.start):
                    self.assertTrue(all(color == TRANSPARENT for color in _row_colors(img, y)))
                ink_rows = _ink_rows(img)
                self.assertTrue(ink_rows & set(top))
                self.assertTrue(ink_rows & set(bottom))
                self.assertTrue(ink_rows <= set(top) | set(bottom))

    def test_no_bar_track(self):
        img = crisp_mod.create_icon_image(50, 50, size=16)

        self.assertNotIn(crisp_mod.ICON_LIGHT['fg_half'], _colors(img))

    def test_top_row_unaffected_by_bottom_exhaustion(self):
        exhausted = crisp_mod.create_icon_image(20, 100, size=16)
        normal = crisp_mod.create_icon_image(20, 5, size=16)
        height = crisp_mod.icon_layout(16).rows.height

        self.assertEqual(exhausted.crop((0, 0, 16, height)).tobytes(), normal.crop((0, 0, 16, height)).tobytes())

    def test_exhausted_row_cross_vs_dollar(self):
        cross = crisp_mod.create_icon_image(100, 20, size=16)
        dollar = crisp_mod.create_icon_image(100, 20, extra_usage_available=True, size=16)

        self.assertNotEqual(cross.tobytes(), dollar.tobytes())

    def test_collapsed_states_fill_the_icon(self):
        """Both exhausted (cross or '$') and both idle ('C') draw one glyph taller than a row."""
        row_height = crisp_mod.icon_layout(16).rows.height
        for pct, extra in ((100, False), (100, True), (0, False)):
            with self.subTest(pct=pct, extra=extra):
                img = crisp_mod.create_icon_image(pct, pct, extra_usage_available=extra, size=16)
                fg_rows = _rows_with_color(img, crisp_mod.ICON_LIGHT['fg'])
                self.assertGreater(max(fg_rows) - min(fg_rows) + 1, row_height)

    def test_single_zero_row_shows_digit(self):
        """Only both-zero collapses to 'C' - a single zero row beside usage renders '0'."""
        img = crisp_mod.create_icon_image(0, 50, size=16)
        top, _bottom = self._rows(16)

        self.assertTrue(_ink_rows(img, top))

    def test_time_and_mode_have_no_effect(self):
        plain = crisp_mod.create_icon_image(70, 20, size=16)
        with_time = crisp_mod.create_icon_image(70, 20, mode_top='overage', time_pct_top=40, time_pct_bottom=40, size=16)

        self.assertEqual(plain.tobytes(), with_time.tobytes())

    def test_99_5_clamps_per_row(self):
        reference = crisp_mod.create_icon_image(10.0, 99.0, size=16)

        self.assertEqual(crisp_mod.create_icon_image(10.0, 99.7, size=16).tobytes(), reference.tobytes())

    def test_light_taskbar_palette(self):
        img = crisp_mod.create_icon_image(50, 50, light_taskbar=True, size=16)

        self.assertIn(crisp_mod.ICON_DARK['fg'], _colors(img))


class TestStatusImage(unittest.TestCase):
    """Error and authentication status icons."""

    def test_variants_differ(self):
        self.assertNotEqual(crisp_mod.create_status_image('!', size=16).tobytes(), crisp_mod.create_status_image('C!', size=16).tobytes())

    def test_vertically_centered(self):
        for size in TRAY_SIZES:
            with self.subTest(size=size):
                rows = _rows_with_color(crisp_mod.create_status_image('!', size=size), crisp_mod.ICON_LIGHT['fg_dim'])
                self.assertLessEqual(abs(min(rows) - (size - 1 - max(rows))), 1)

    def test_light_taskbar_palette(self):
        img = crisp_mod.create_status_image('!', light_taskbar=True, size=16)

        self.assertIn(crisp_mod.ICON_DARK['fg_dim'], _colors(img))


if __name__ == '__main__':
    unittest.main()
