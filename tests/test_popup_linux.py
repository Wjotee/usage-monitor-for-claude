"""
Linux Popup Host Tests
=======================

Unit tests for the GTK popup host: anchoring, the map-then-move ordering,
dismissal on focus loss or Escape, and the pinned-popup drag.  The host
defers every ``gi`` import, so these tests run without PyGObject.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

if sys.platform == 'win32':
    raise unittest.SkipTest('GTK popup host is not used on Windows')

from usage_monitor_for_claude.platforms.popup_linux import (  # noqa: E402
    _FOCUS_SETTLE_SECONDS, _KEY_ESCAPE, _MARGIN, REUSE_WINDOW, WINDOW_KWARGS, PopupHost, popup_url,
)

WIDTH = 340


def _host(window=None, gtk_window=None):
    """Build a host whose GTK calls run inline instead of on a main loop."""
    host = PopupHost(window or MagicMock(), WIDTH)
    gtk_window = gtk_window or MagicMock()
    host._gtk_window = lambda: gtk_window
    host._on_main_loop = lambda action: action(gtk_window)

    return host, gtk_window


def _monitor(work_x=2560, work_y=32, work_width=3440, work_height=1357, monitor_y=0, monitor_height=None):
    """Build a monitor; without *monitor_height* the work area reaches the monitor's bottom edge."""
    if monitor_height is None:
        monitor_height = work_y - monitor_y + work_height

    monitor = MagicMock()
    monitor.get_workarea.return_value = MagicMock(x=work_x, y=work_y, width=work_width, height=work_height)
    monitor.get_geometry.return_value = MagicMock(y=monitor_y, height=monitor_height)

    return monitor


def _bottom_panel_monitor():
    """A monitor whose only panel reserves 48 pixels at the bottom edge."""
    return _monitor(work_y=0, work_height=1032, monitor_height=1080)


class TestWindowOptions(unittest.TestCase):
    """Tests for the platform's window creation options."""

    def test_popup_url_is_a_file_uri(self):
        """A bare path leaves WebKitGTK on about:blank, so a real URI is required."""
        self.assertEqual(popup_url(Path('/opt/app/popup.html')), 'file:///opt/app/popup.html')

    def test_window_is_resizable(self):
        """GTK ignores resize() on a non-resizable window."""
        self.assertTrue(WINDOW_KWARGS['resizable'])


class TestAnchor(unittest.TestCase):
    """Tests for placement in the work area's tray corner."""

    def test_anchors_to_top_right_of_work_area(self):
        """The panel sits at the top, so the popup hangs below its right corner."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            self.assertEqual(host._anchor(), (2560 + 3440 - WIDTH - _MARGIN, 32 + _MARGIN))

    def test_anchors_to_bottom_right_above_a_bottom_panel(self):
        """Space reserved at the bottom and none at the top puts the tray in the bottom corner."""
        host, _ = _host()
        host._height = 472
        with patch.object(host, '_primary_monitor', return_value=_bottom_panel_monitor()):
            self.assertEqual(host._anchor(), (2560 + 3440 - WIDTH - _MARGIN, 1032 - 472 - _MARGIN))

    def test_offset_monitor_with_bottom_panel_anchors_to_bottom(self):
        """The work area is compared against the monitor's own edges, not against zero."""
        host, _ = _host()
        host._height = 472
        monitor = _monitor(work_y=300, work_height=1032, monitor_y=300, monitor_height=1080)
        with patch.object(host, '_primary_monitor', return_value=monitor):
            _x, y = host._anchor()
        self.assertEqual(y, 300 + 1032 - 472 - _MARGIN)

    def test_offset_monitor_with_top_panel_anchors_to_top(self):
        """A top panel is recognized on a monitor that does not start at the root origin."""
        host, _ = _host()
        host._height = 472
        with patch.object(host, '_primary_monitor', return_value=_monitor(work_y=332, monitor_y=300)):
            _x, y = host._anchor()
        self.assertEqual(y, 332 + _MARGIN)

    def test_work_area_equal_to_the_monitor_keeps_the_top_corner(self):
        """A fullscreen window, an auto-hiding panel and a missing primary monitor all report no reserved space."""
        host, _ = _host()
        host._height = 472
        with patch.object(host, '_primary_monitor', return_value=_monitor(work_y=0, work_height=1080)):
            _x, y = host._anchor()
        self.assertEqual(y, _MARGIN)

    def test_side_panel_only_keeps_the_top_corner(self):
        """A panel on the left or right edge says nothing about top or bottom."""
        host, _ = _host()
        host._height = 472
        monitor = _monitor(work_x=2560 + 64, work_y=0, work_width=3440 - 64, work_height=1080)
        with patch.object(host, '_primary_monitor', return_value=monitor):
            self.assertEqual(host._anchor(), (2560 + 3440 - WIDTH - _MARGIN, _MARGIN))

    def test_top_and_bottom_panels_keep_the_top_corner(self):
        """With a panel on both edges the tray sits in the top one."""
        host, _ = _host()
        host._height = 472
        monitor = _monitor(work_y=32, work_height=1000, monitor_height=1080)
        with patch.object(host, '_primary_monitor', return_value=monitor):
            _x, y = host._anchor()
        self.assertEqual(y, 32 + _MARGIN)

    def test_anchor_uses_absolute_coordinates(self):
        """Gtk.Window.move() takes root coordinates, so the monitor origin is included."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor(work_x=0)):
            x, _y = host._anchor()
        self.assertEqual(x, 3440 - WIDTH - _MARGIN)

    def test_anchor_without_display(self):
        """No display means no anchor rather than a crash."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=None):
            self.assertIsNone(host._anchor())


class TestPrepareAndReveal(unittest.TestCase):
    """Tests for the invisible-measure-then-show sequence."""

    def test_prepare_hides_and_shows(self):
        """The window goes on screen at zero opacity so the layout can settle."""
        window = MagicMock()
        host, gtk_window = _host(window)
        host.prepare()
        gtk_window.set_opacity.assert_called_once_with(0.0)
        window.show.assert_called_once()

    def test_reveal_maps_before_moving(self):
        """A position set on an unmapped window is discarded by the compositor."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()

        order = [name for name, _args, _kwargs in gtk_window.method_calls]
        self.assertLess(order.index('show_all'), order.index('move'))
        self.assertLess(order.index('present'), order.index('move'))

    def test_reveal_positions_a_bottom_anchored_popup_with_its_height(self):
        """Without the height from apply_geometry() the popup's top edge would land on the panel."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_bottom_panel_monitor()):
            host.apply_geometry(472, keep_position=False)
            host.reveal()

        self.assertEqual(gtk_window.move.call_args_list[-1].args, (2560 + 3440 - WIDTH - _MARGIN, 1032 - 472 - _MARGIN))

    def test_reveal_forces_focus(self):
        """Without focus the window can never emit focus-out, so it could never dismiss."""
        host, gtk_window = _host()
        gdk_window = MagicMock()
        gtk_window.get_window.return_value = gdk_window
        fake_gdk = MagicMock()
        fake_gdk.CURRENT_TIME = 0

        with patch.object(host, '_primary_monitor', return_value=_monitor()), \
             patch.dict(sys.modules, {'gi.repository': MagicMock(Gdk=fake_gdk)}):
            host.reveal()

        gdk_window.focus.assert_called_once()

    def test_reveal_makes_the_window_opaque_last(self):
        """Opacity is restored only after the window sits where it belongs."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()

        self.assertEqual(gtk_window.set_opacity.call_args, call(1.0))
        order = [name for name, _args, _kwargs in gtk_window.method_calls]
        self.assertLess(order.index('move'), order.index('set_opacity'))

    def test_reveal_keeps_position_when_asked(self):
        """A dragged pinned popup must not snap back on reveal."""
        host, gtk_window = _host()
        host._keep_position = True
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()
        gtk_window.move.assert_not_called()


class TestApplyGeometry(unittest.TestCase):
    """Tests for resizing and repositioning."""

    def test_resize_uses_logical_pixels(self):
        """GTK reports and consumes logical pixels, so no DPI arithmetic is needed."""
        window = MagicMock()
        host, _ = _host(window)
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.apply_geometry(565, keep_position=False)
        window.resize.assert_called_once_with(WIDTH, 565)

    def test_moves_to_anchor(self):
        """An unpinned popup follows the work-area corner."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.apply_geometry(565, keep_position=False)
        gtk_window.move.assert_called_once_with(2560 + 3440 - WIDTH - _MARGIN, 32 + _MARGIN)

    def test_bottom_anchor_follows_the_new_height(self):
        """Above a bottom panel a taller popup has to move up, or it would grow over the panel."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_bottom_panel_monitor()):
            host.apply_geometry(565, keep_position=False)
        gtk_window.move.assert_called_once_with(2560 + 3440 - WIDTH - _MARGIN, 1032 - 565 - _MARGIN)

    def test_kept_position_resizes_without_moving(self):
        """A moved pinned popup keeps its position when the content height changes."""
        window = MagicMock()
        host, gtk_window = _host(window)
        host.apply_geometry(565, keep_position=True)
        window.resize.assert_called_once_with(WIDTH, 565)
        gtk_window.move.assert_not_called()


class TestDismissWatch(unittest.TestCase):
    """Tests for dismissal on focus loss and Escape."""

    def _watching(self, should_dismiss):
        host, gtk_window = _host()
        running = threading.Event()
        running.set()
        thread = threading.Thread(
            target=lambda: host.watch_dismiss(should_dismiss, running.is_set), daemon=True,
        )
        thread.start()
        self.addCleanup(host.stop_watch)

        return host, gtk_window, thread

    def test_connects_both_signals(self):
        """Focus loss covers clicks elsewhere, Escape covers the keyboard."""
        host, gtk_window, _thread = self._watching(lambda: True)
        connected = {args[0] for name, args, _kwargs in gtk_window.method_calls if name == 'connect'}
        self.assertEqual(connected, {'focus-out-event', 'key-press-event'})

    def test_focus_out_dismisses(self):
        """Losing focus closes an unpinned popup."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_focus_out()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_focus_out_ignored_while_pinned(self):
        """A pinned popup stays open when focus moves elsewhere."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host._on_focus_out()
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_escape_dismisses(self):
        """Escape closes an unpinned popup."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE))
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_other_keys_are_ignored(self):
        """Only Escape dismisses; the content keeps every other key."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_key_press(None, MagicMock(keyval=0x61))  # 'a'
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_escape_ignored_while_pinned(self):
        """A pinned popup ignores Escape as well."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE))
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_stop_watch_releases_the_wait(self):
        """Closing the window must end the watch even while pinned."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host.stop_watch()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_signal_handlers_never_swallow_the_event(self):
        """Returning True would stop GTK from delivering the event elsewhere."""
        host, _gtk_window, _thread = self._watching(lambda: True)
        self.assertFalse(host._on_focus_out())
        self.assertFalse(host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE)))

    def test_focus_out_right_after_the_reveal_is_ignored(self):
        """The compositor reports one for a window it has just mapped; that is not the user leaving."""
        host, _gtk_window, thread = self._watching(lambda: True)
        with patch('usage_monitor_for_claude.platforms.popup_linux.time.monotonic', return_value=100.0):
            host._focus_settled_at = 100.0 + _FOCUS_SETTLE_SECONDS
            host._on_focus_out()
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_focus_out_after_the_settle_period_dismisses(self):
        """Once the window has settled, losing focus closes an unpinned popup again."""
        host, _gtk_window, thread = self._watching(lambda: True)
        with patch('usage_monitor_for_claude.platforms.popup_linux.time.monotonic', return_value=100.0):
            host._focus_settled_at = 100.0
            host._on_focus_out()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def _focus_out_in_settle(self, should_dismiss=True):
        """Deliver a focus-out inside the settle period; return the host, window and the scheduled check."""
        host, gtk_window = _host()
        host._should_dismiss = lambda: should_dismiss
        fake_glib = MagicMock()
        with patch('usage_monitor_for_claude.platforms.popup_linux.time.monotonic', return_value=100.0), \
             patch.dict(sys.modules, {'gi.repository': MagicMock(GLib=fake_glib)}):
            host._focus_settled_at = 100.0 + _FOCUS_SETTLE_SECONDS
            host._on_focus_out(gtk_window)

        return host, gtk_window, fake_glib

    def test_focus_out_in_the_settle_period_schedules_a_recheck(self):
        """The recheck fires just after the period, so a user who clicked away at once is not left with an open popup."""
        _host_, _gtk_window, fake_glib = self._focus_out_in_settle()
        delay, _check = fake_glib.timeout_add.call_args.args
        self.assertEqual(delay, round(_FOCUS_SETTLE_SECONDS * 1000) + 50)

    def test_recheck_dismisses_a_window_that_still_has_no_focus(self):
        host, gtk_window, fake_glib = self._focus_out_in_settle()
        gtk_window.has_toplevel_focus.return_value = False
        fake_glib.timeout_add.call_args.args[1]()
        self.assertTrue(host._dismissed.is_set())

    def test_recheck_keeps_a_window_that_regained_focus(self):
        """The compositor's focus-out for a freshly mapped window is followed by the focus coming back."""
        host, gtk_window, fake_glib = self._focus_out_in_settle()
        gtk_window.has_toplevel_focus.return_value = True
        fake_glib.timeout_add.call_args.args[1]()
        self.assertFalse(host._dismissed.is_set())

    def test_recheck_keeps_a_pinned_popup(self):
        host, gtk_window, fake_glib = self._focus_out_in_settle(should_dismiss=False)
        gtk_window.has_toplevel_focus.return_value = False
        fake_glib.timeout_add.call_args.args[1]()
        self.assertFalse(host._dismissed.is_set())

    def test_recheck_of_an_earlier_opening_is_ignored(self):
        """A check left over from the previous opening must not close the popup that replaced it."""
        host, gtk_window, fake_glib = self._focus_out_in_settle()
        gtk_window.has_toplevel_focus.return_value = False
        host._reveal_count += 1
        fake_glib.timeout_add.call_args.args[1]()
        self.assertFalse(host._dismissed.is_set())

    def test_focus_out_without_a_window_schedules_nothing(self):
        host, _gtk_window = _host()
        fake_glib = MagicMock()
        with patch('usage_monitor_for_claude.platforms.popup_linux.time.monotonic', return_value=100.0), \
             patch.dict(sys.modules, {'gi.repository': MagicMock(GLib=fake_glib)}):
            host._focus_settled_at = 100.0 + _FOCUS_SETTLE_SECONDS
            host._on_focus_out()
        fake_glib.timeout_add.assert_not_called()

    def test_reveal_starts_the_settle_period(self):
        """Every opening gets its own grace period, not only the first."""
        host, _gtk_window = _host()
        with patch.object(host, '_anchor', return_value=None), \
             patch('usage_monitor_for_claude.platforms.popup_linux.time.monotonic', return_value=50.0):
            host.reveal()
        self.assertEqual(host._focus_settled_at, 50.0 + _FOCUS_SETTLE_SECONDS)

    def test_signals_are_connected_once_across_watches(self):
        """The window is kept between openings, so a second watch must not stack handlers."""
        host, gtk_window = _host()
        host._connect_dismiss_signals(gtk_window)
        host._connect_dismiss_signals(gtk_window)
        connects = [name for name, _args, _kwargs in gtk_window.method_calls if name == 'connect']
        self.assertEqual(len(connects), 2)


class TestHide(unittest.TestCase):
    """Tests for taking the popup off screen without destroying its window."""

    def test_hide_hides_the_gtk_window(self):
        """The window stays alive; it is only unmapped."""
        window = MagicMock()
        host, gtk_window = _host(window)
        host.hide()
        gtk_window.hide.assert_called_once()
        window.destroy.assert_not_called()

    def test_hide_ends_a_drag_in_progress(self):
        """A drag left over from the previous opening must not carry into the next."""
        host, _gtk_window = _host()
        host._dragging = True
        host.hide()
        self.assertFalse(host._dragging)

    def test_this_platform_reuses_its_window(self):
        """GTK leaves a web and network process behind for every destroyed window."""
        self.assertTrue(REUSE_WINDOW)


class TestDrag(unittest.TestCase):
    """Tests for the pinned-popup drag."""

    def _host_with_pointer(self, pointer, origin):
        host, gtk_window = _host()
        host._pointer_and_origin = lambda: (*pointer, *origin)

        return host, gtk_window

    def test_begin_drag_records_the_grab_offset(self):
        host, _gtk_window = self._host_with_pointer((700, 620), (660, 580))
        self.assertTrue(host.begin_drag())
        self.assertEqual(host._drag_offset, (40, 40))
        self.assertTrue(host._dragging)

    def test_begin_drag_without_pointer_is_refused(self):
        """No display means no pointer to anchor to."""
        host, _gtk_window = _host()
        host._pointer_and_origin = lambda: None
        self.assertFalse(host.begin_drag())

    def test_drag_ignored_when_not_dragging(self):
        host, gtk_window = self._host_with_pointer((700, 620), (660, 580))
        self.assertFalse(host.drag())
        gtk_window.move.assert_not_called()

    def test_drag_moves_to_pointer_minus_offset(self):
        """Each step is absolute, so out-of-order calls converge instead of drifting."""
        host, gtk_window = self._host_with_pointer((700, 620), (660, 580))
        host.begin_drag()
        host._pointer_and_origin = lambda: (900, 700, 660, 580)
        self.assertTrue(host.drag())
        gtk_window.move.assert_called_once_with(860, 660)

    def test_end_drag_clears_the_flag(self):
        host, _gtk_window = self._host_with_pointer((700, 620), (660, 580))
        host.begin_drag()
        host.end_drag(565)
        self.assertFalse(host._dragging)
        self.assertEqual(host._height, 565)


if __name__ == '__main__':
    unittest.main()
