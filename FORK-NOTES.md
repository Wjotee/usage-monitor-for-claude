# Fork Notes

This fork of [jens-duttke/usage-monitor-for-claude](https://github.com/jens-duttke/usage-monitor-for-claude) replaces the Windows tray icon renderer with one that draws at the exact tray slot size (16 px at 100% scaling, 20 / 24 / 32 px at 125% / 150% / 200%), so the icon is never resampled and stays sharp. Linux keeps the original renderer.

- **Digits and symbols** use the taskbar clock's typeface (Segoe UI, loaded from `%WINDIR%\Fonts\segoeui.ttf`) at the clock's digit height: 8 px at 100% scaling. That covers the numbers, the idle `C`, `$`, `!` and `C!`. They are emboldened slightly to match how heavily Windows draws the clock (`CLOCK_STROKE_RATIO` in `crisp_tray_icon.py`). If the font is missing, built-in pixel shapes are used instead.
- **The exhausted cross** is drawn as an `X` in the same font, because Segoe UI has no `✕` glyph (it renders as an empty box).
- **`$`** stands taller than the digits, so it is sized by its own height rather than clipped to the digit height (the `reference` argument of `_clock_digit_font` / `_draw_clock_digits`).
- **Bars and the time marker** are whole-pixel shapes with hard edges.

## Files

### Mine (new files - upstream never touches them)

| File | Purpose |
|---|---|
| `usage_monitor_for_claude/crisp_tray_icon.py` | The renderer. Same `create_icon_image` / `create_status_image` signatures as `tray_icon.py`. |
| `usage_monitor_for_claude/platforms/tray_pixels.py` | Picks the Windows or Linux backend below. |
| `usage_monitor_for_claude/platforms/tray_pixels_win32.py` | Tray slot size from the taskbar's DPI; builds the icon handle straight from the pixels (replaces pystray's `.ico` round trip); loads the clock's typeface. |
| `usage_monitor_for_claude/platforms/tray_pixels_linux.py` | No-op: reports "no fixed size", so the original renderer is used. |
| `tests/test_crisp_tray_icon.py`, `tests/test_tray_pixels.py` | Tests for the above. |
| `tools/preview_tray_icon.py` | Writes enlarged before/after PNG previews: `python tools/preview_tray_icon.py ..\icon-previews` |
| `FORK-NOTES.md` | This file. |

### Original files I touched

Only `usage_monitor_for_claude/app.py`, two lines:

```diff
@@ line 37 (imports) @@
-from .tray_icon import create_icon_image, create_status_image
+from .crisp_tray_icon import create_icon_image, create_status_image, install_exact_icon_handle

@@ line 181 (right after `self.icon = pystray.Icon(...)` closes) @@
+        install_exact_icon_handle(self.icon)
```

`tray_icon.py` itself is untouched and still used on Linux and as the fallback.

## Pulling upstream updates

Run from the repository folder, in PowerShell:

```powershell
git fetch upstream
git checkout main
git merge upstream/main
```

**If there is a conflict** it will almost certainly be in `app.py`. Keep upstream's version of everything, then reapply the two lines above. `rerere` is enabled, so git replays a resolution it has seen before. Then:

```powershell
git add usage_monitor_for_claude/app.py
git commit
```

**Then test, rebuild and push:**

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade
python -m unittest discover -s tests
python tools/preview_tray_icon.py ..\icon-previews
python build.py
git push origin main
```

The EXE is written to `dist\UsageMonitorForClaude.exe`. Quit the running app before replacing its EXE.

## What to check in the upstream CHANGELOG

`crisp_tray_icon.py` reimplements the drawing rules of `tray_icon.py` rather than calling them, so upstream changes there do **not** flow into the fork automatically. After each merge, run `git diff ORIG_HEAD -- usage_monitor_for_claude/tray_icon.py` and look in `CHANGELOG.md` for:

- **Tray icon behavior**: new or changed glyphs or states (anything besides the digits and the font-drawn symbols `C`, `$`, the cross, `!`, `C!`), bar or marker rules, warning colors, overage mode, clamping at 99. Port the change into `crisp_tray_icon.py` and its tests.
- **New icon styles** or settings: `icon_style`, `icon_fields`, `icon_light` / `icon_dark` colors. A new `icon_style` value would silently fall through to the classic layout here.
- **Signature changes** to `create_icon_image` / `create_status_image`, or new call sites in `app.py`. The import swap only covers names the fork exports.
- **pystray upgrades** (`requirements.txt`): the fork replaces pystray's private `_assert_icon_handle` and reads `_icon_handle`. If pystray renames them, `install_exact_icon_handle` returns False and the icon quietly goes back to the blurry stock path. `tests/test_tray_pixels.py` exercises the swap against a stand-in object, so also check `pystray/_win32.py` by eye after an upgrade.
- **DPI handling** (`set_dpi_awareness`, the spec's manifest): the slot size relies on the process staying Per-Monitor-V2 aware.

Not upstream, but worth knowing: on **Windows 11** the clock uses Segoe UI Variable (`SegUIVar.ttf`) instead of Segoe UI. To match it there, change `_CLOCK_FONT_FILE` in `tray_pixels_win32.py`.

After changing display scaling, the icon picks up the new size at its next refresh (the next poll, or a theme change).
