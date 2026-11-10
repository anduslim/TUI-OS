"""Kivy host for the Android APK.

The whole window is one :class:`TerminalView` that paints the
:class:`~tui_os.host.bridge.TerminalBridge` cell grid and feeds it the
hardware keyboard and taps. No Android terminal or shell is involved: the
Textual app runs on a thread inside this process.

E-ink behaviour:
* Rows are repainted only when the bridge reports them dirty. Kivy only
  redraws the GL surface when canvas instructions change, so an idle TUI
  leaves the panel untouched.
* Text comes from a glyph atlas (:mod:`.atlas`) and each row is a handful of
  meshes, so the canvas stays small and a new screen renders few glyphs.
* Painting stops after ``PAINT_BUDGET_S`` per frame and resumes on the next
  one, so a full-screen change can never hold the UI thread long enough
  for Android to call the app unresponsive.
* A vertical swipe sends PgUp/PgDn (page-based navigation) instead of
  scrolling smoothly.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

from kivy.config import Config  # noqa: E402

# Must run before kivy.core.window is imported.
Config.set("kivy", "exit_on_escape", "0")  # Esc / BACK belongs to the TUI
Config.set("kivy", "keyboard_mode", "system")  # no on-screen Kivy keyboard
Config.set("graphics", "maxfps", "30")
Config.set("input", "mouse", "mouse,disable_multitouch")

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.text import Label as CoreLabel  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.graphics import Color, InstructionGroup, Mesh, Rectangle  # noqa: E402
from kivy.uix.widget import Widget  # noqa: E402
from kivy.utils import platform  # noqa: E402
from rich.cells import cell_len, get_character_cell_size  # noqa: E402

from .atlas import AtlasFull, GlyphAtlas  # noqa: E402
from .bridge import DEFAULT_BG, Run, TerminalBridge  # noqa: E402
from .glyphs import DRAWN, cell_rects  # noqa: E402
from .grid import GRID_COLS, GRID_ROWS, GridGeometry  # noqa: E402
from .keymap import PASTE_KEYS, CharFallback, CharKey, NamedKey, translate_keydown  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "assets" / "fonts"
FONT_REGULAR = FONT_DIR / "JetBrainsMonoNL-Regular.ttf"
FONT_BOLD = FONT_DIR / "JetBrainsMonoNL-Bold.ttf"
BUNDLED_CONFIG = ROOT / "android" / "config.toml"

OVERRIDE_NAME = "config.toml"
OVERRIDE_HEADER = """\
# Rename to config.toml (same folder) to override the app's settings, e.g.
#   adb pull /sdcard/Android/data/org.olares.olares_workspace_tui/files/config.toml.example config.toml
#   (edit)  adb push config.toml /sdcard/Android/data/org.olares.olares_workspace_tui/files/
# then force-stop and reopen the app. Keys here win over the bundled ones;
# leave out what you don't change. Remove the file to go back to the defaults.
# Put plain settings ABOVE any [queries] line: TOML puts every key after a
# [table] header inside that table.
#
# Mail / Calendar secrets from Vaultwarden (see README):
#   vault_url = "https://vaultwarden.you.olares.com"
#   vault_email = "you@example.com"
#
# Local LLM on llama.cpp (OpenAI-compatible API):
#   llm_api = "openai"
#   llm_url = "http://192.168.1.50:8080"   # or the app's https URL on Olares
#   llm_model = ""                         # empty = whatever the server loaded
#   llm_api_key = ""
#
# ---- bundled defaults ----
"""

REFRESH_HZ = 20
#: Most UI-thread time spent painting rows in one frame; the rest waits for
#: the next frame. Android declares an app unresponsive after 5 s.
PAINT_BUDGET_S = 0.03
#: Printable ASCII, pre-rendered into the atlas while the UI is idle.
WARM_GLYPHS = [chr(c) for c in range(33, 127)]
SWIPE_ROWS = 3
#: Hold a finger this long (seconds) without moving to paste the clipboard.
LONG_PRESS_S = 0.6
TEXT_FALLBACK_DELAY = 0.06


_RGB: dict[str, tuple[float, float, float, float]] = {}


def _rgb(hex6: str) -> tuple[float, float, float, float]:
    rgb = _RGB.get(hex6)
    if rgb is None:
        rgb = _RGB[hex6] = (int(hex6[0:2], 16) / 255, int(hex6[2:4], 16) / 255, int(hex6[4:6], 16) / 255, 1.0)
    return rgb


class _Quads:
    """Vertices and indices for one mesh (x, y, u, v per vertex)."""

    __slots__ = ("vertices", "indices")

    def __init__(self) -> None:
        self.vertices: list[float] = []
        self.indices: list[int] = []

    def add(self, x0: float, y0: float, x1: float, y1: float,
            u0: float = 0.0, v0: float = 0.0, u1: float = 0.0, v1: float = 0.0) -> None:
        """Quad from (x0, y0) bottom-left to (x1, y1) top-right; v0 maps to the top."""
        i = len(self.vertices) // 4
        self.vertices.extend((x0, y0, u0, v1, x1, y0, u1, v1, x1, y1, u1, v0, x0, y1, u0, v0))
        self.indices.extend((i, i + 1, i + 2, i + 2, i + 3, i))


class TerminalView(Widget):
    """Paints the bridge's fixed 147×46 cell grid, scaled to fit the window."""

    def __init__(self, bridge: TerminalBridge, **kw) -> None:
        super().__init__(**kw)
        self.bridge = bridge
        self.on_geometry = None  # set by the app: re-place native overlays
        self.trace = None  # set by the app when debug_input is on
        self.grid = GridGeometry.fit(1, 1)
        self._fonts = {False: str(FONT_REGULAR), True: str(FONT_BOLD)}
        self._rows: dict[int, InstructionGroup] = {}
        self._atlas: GlyphAtlas | None = None
        #: Rows waiting to be painted (newest runs win), when a frame ran out
        #: of budget.
        self._backlog: dict[int, list[Run]] = {}
        self._warm: list[tuple[str, bool]] = []
        self._touch_start: tuple[float, float] | None = None
        self._touch_t0 = 0.0
        with self.canvas.before:
            Color(*_rgb(DEFAULT_BG))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._on_geometry, size=self._on_geometry)
        self._splash: InstructionGroup | None = None
        self._splash_text = ""
        Clock.schedule_interval(self._poll, 1 / REFRESH_HZ)

    # ------------------------------------------------------------ geometry
    def grid_size(self) -> tuple[int, int]:
        return GRID_COLS, GRID_ROWS

    def _on_geometry(self, *_a) -> None:
        self._bg.pos, self._bg.size = self.pos, self.size
        grid = GridGeometry.fit(self.width, self.height)
        if grid.font_px != self.grid.font_px:
            self._atlas = None  # glyph size changed: re-render on demand
        self.grid = grid
        if self._splash is not None:
            self.show_splash(self._splash_text)  # re-centre
        self.bridge.resize(GRID_COLS, GRID_ROWS)
        self.full_redraw()
        if self.on_geometry is not None:
            self.on_geometry()

    def full_redraw(self) -> None:
        for group in self._rows.values():
            self.canvas.remove(group)
        self._rows.clear()
        self._backlog.clear()
        self.bridge.mark_all_dirty()

    def cell_at(self, x: float, y: float) -> tuple[int, int]:
        return self.grid.cell_at(x - self.x, self.top - y)

    def parent_top_offset(self) -> float:
        """Distance from the window's top edge to this widget's top (px)."""
        return Window.height - self.top

    # ------------------------------------------------------------- painting
    def show_splash(self, text: str) -> None:
        """Centered start-up line (e.g. "TUI-OS 1.1.6 · starting…") until the
        TUI paints its first frame."""
        self.hide_splash()
        self._splash_text = text
        label = CoreLabel(text=text, font_size=max(18, self.grid.font_px * 2),
                          font_name=self._fonts[True], color=_rgb("00FF00"))
        label.refresh()
        tex = label.texture
        g = InstructionGroup()
        g.add(Color(1, 1, 1, 1))
        g.add(Rectangle(texture=tex, size=tex.size,
                        pos=(self.center_x - tex.width / 2, self.center_y - tex.height / 2)))
        self.canvas.after.add(g)
        self._splash = g

    def hide_splash(self) -> None:
        if self._splash is not None:
            self.canvas.after.remove(self._splash)
            self._splash = None

    def _poll(self, _dt: float) -> None:
        rows = self.bridge.take_dirty_rows()
        if self._splash is not None and any(r.text.strip() for _, runs in rows for r in runs):
            self.hide_splash()  # the TUI drew real content
        for y, runs in rows:
            self._backlog[y] = runs
        started = time.perf_counter()
        deadline = started + PAINT_BUDGET_S
        if self._backlog:
            painted = 0
            for y in sorted(self._backlog):
                painted += 1
                runs = self._backlog.pop(y)
                try:
                    self._paint_row(y, runs)
                except AtlasFull:
                    # Thousands of distinct glyphs: start a fresh atlas and
                    # repaint everything from it.
                    self._atlas = None
                    self.full_redraw()
                    return
                if time.perf_counter() > deadline:
                    break  # the rest is painted on the next frame
            if self.trace is not None:
                self.trace(f"paint {painted} rows in {(time.perf_counter() - started) * 1000:.0f} ms"
                           f" ({len(self._backlog)} left)")
        elif self._warm:
            self._warm_up(deadline)

    def _ensure_atlas(self) -> GlyphAtlas:
        if self._atlas is None:
            self._atlas = GlyphAtlas(self.grid.font_px, self._fonts)
            self._warm = [(ch, bold) for bold in (False, True) for ch in WARM_GLYPHS]
        return self._atlas

    def _warm_up(self, deadline: float) -> None:
        """Render common glyphs ahead of time, a few per idle frame."""
        atlas = self._ensure_atlas()
        while self._warm and time.perf_counter() < deadline:
            ch, bold = self._warm.pop()
            if (ch, bold) not in atlas:
                atlas.get(ch, bold)

    def _paint_row(self, row: int, runs: list[Run]) -> None:
        atlas = self._ensure_atlas()
        g = self.grid
        left, top = self.x, self.top
        y0, y1 = g.row_edge(row), g.row_edge(row + 1)
        # Kivy's y axis points up: the row spans [by0, by1) on screen.
        by0, by1 = top - y1, top - y0
        cell_h = y1 - y0
        bgs: dict[str, _Quads] = {}
        solids: dict[str, _Quads] = {}
        texts: dict[str, _Quads] = {}
        col_edge = g.col_edge
        for run in runs:
            text = run.text
            x0 = col_edge(run.col)
            x1 = col_edge(run.col + (len(text) if text.isascii() else cell_len(text)))
            if run.bg != DEFAULT_BG:
                bgs.setdefault(run.bg, _Quads()).add(left + x0, by0, left + x1, by1)
            fg = run.fg
            text_q = solid_q = None
            col = run.col
            for ch in text:
                if ch == " ":
                    col += 1
                    continue
                cx0 = col_edge(col)
                if ch in DRAWN:
                    if solid_q is None:
                        solid_q = solids.setdefault(fg, _Quads())
                    for rx, ry, rw, rh in cell_rects(ch, cx0, y0, col_edge(col + 1), y1, g.scale):
                        solid_q.add(left + rx, top - ry - rh, left + rx + rw, top - ry)
                    col += 1
                    continue
                glyph = atlas.get(ch, run.bold)
                if glyph is not None:
                    w, h, u0, v0, u1, v1 = glyph
                    if text_q is None:
                        text_q = texts.setdefault(fg, _Quads())
                    # Centre vertically in the cell; whole pixels keep glyphs sharp.
                    gy = by1 - (cell_h - h) // 2 - h
                    gx = left + cx0
                    text_q.add(gx, gy, gx + w, gy + h, u0, v0, u1, v1)
                col += 1 if ch.isascii() else get_character_cell_size(ch)
            if run.underline:
                t = max(1, round(g.scale))
                solids.setdefault(fg, _Quads()).add(left + x0, by0 + t, left + x1, by0 + 2 * t)

        group = InstructionGroup()
        for colours, texture in ((bgs, None), (solids, None), (texts, atlas.texture)):
            for colour, quads in colours.items():
                group.add(Color(*_rgb(colour)))
                group.add(Mesh(vertices=quads.vertices, indices=quads.indices,
                               mode="triangles", texture=texture))
        old = self._rows.pop(row, None)
        if old is not None:
            self.canvas.remove(old)
        self.canvas.add(group)
        self._rows[row] = group

    # ---------------------------------------------------------------- touch
    def on_touch_down(self, touch) -> bool:
        if self.collide_point(*touch.pos):
            self._touch_start = touch.pos
            self._touch_t0 = time.monotonic()
            return True
        return False

    def on_touch_up(self, touch) -> bool:
        if self._touch_start is None:
            return False
        sx, sy = self._touch_start
        self._touch_start = None
        dy = (touch.y - sy) / self.grid.cell_h
        if abs(dy) >= SWIPE_ROWS and abs(touch.y - sy) > abs(touch.x - sx):
            # Finger moves up -> content goes forward a page.
            self.bridge.send_key("pagedown" if dy > 0 else "pageup")
        else:
            self.bridge.tap(*self.cell_at(*touch.pos))
            moved = max(abs(touch.x - sx) / self.grid.cell_w, abs(touch.y - sy) / self.grid.cell_h)
            if time.monotonic() - self._touch_t0 >= LONG_PRESS_S and moved < 1:
                # Long press: focus what's under the finger, then paste into it.
                paste_clipboard(self.bridge)
        return True


def paste_clipboard(bridge: TerminalBridge) -> None:
    """Send the Android/desktop clipboard to the TUI as a Paste event."""
    try:
        from kivy.core.clipboard import Clipboard

        text = Clipboard.paste()
    except Exception as exc:  # noqa: BLE001 - no clipboard provider: say so, don't crash
        # Format now: Python unbinds ``exc`` when the except block ends.
        message = f"Clipboard unavailable: {exc}"
        bridge.call_in_app(lambda app: app.notify(message, severity="warning"))
        return
    if not text:
        bridge.call_in_app(lambda app: app.notify("Clipboard is empty."))
        return
    bridge.send_paste(text)


class OlaresWorkspaceApp(App):
    title = "Olares Workspace TUI"

    def build(self) -> TerminalView:
        Window.clearcolor = _rgb(DEFAULT_BG)
        Window.softinput_mode = "below_target"
        self._prepare_config()
        self.bridge = TerminalBridge(self._make_tui, on_exit=self._tui_exited)
        self.view = TerminalView(self.bridge)
        if self._debug_input:
            self.view.trace = self._trace
        self._chars = CharFallback()
        self._swallow_text: tuple[str, float] | None = None
        self._pending_ev = None
        self._keyboard = None
        Window.bind(on_key_down=self._on_key_down, on_textinput=self._on_textinput)
        Window.bind(on_keyboard=self._swallow_back)
        return self.view

    # ----------------------------------------------------------- lifecycle
    def on_start(self) -> None:
        from ..version import app_version

        self.view.show_splash(f"TUI-OS {app_version()} · starting…")
        self.host = _android_host(self.bridge, self.view)
        if self.host is not None:
            self.view.on_geometry = self.host.relayout
        self.bridge.resize(*self.view.grid_size())
        self.bridge.start()
        self._lock_keyboard()
        _enter_immersive_mode()

    def on_pause(self) -> bool:
        return True  # keep the TUI (and its streams) alive in the background

    def on_resume(self) -> None:
        self._lock_keyboard()
        _enter_immersive_mode()
        self.view.full_redraw()  # one clean full repaint after wake = no ghosting

    def on_stop(self) -> None:
        self.bridge.stop(timeout=3)
        if getattr(self, "host", None) is not None:
            self.host.destroy()

    def _make_tui(self, driver_class):
        from ..app import TuiOS
        from ..config import Settings

        return TuiOS(Settings.load(), driver_class=driver_class, host=getattr(self, "host", None))

    def _tui_exited(self, bridge: TerminalBridge) -> None:
        if bridge.error:
            # Don't vanish: show the traceback on the panel and keep it on disk.
            Clock.schedule_once(lambda _dt: show_crash(self, bridge.error), 0)
        else:
            Clock.schedule_once(lambda _dt: self.stop(), 0)

    def _prepare_config(self) -> None:
        """First launch: copy the bundled config.toml to private storage.

        Then layer ``config.toml`` from the external files dir on top, if
        present, so settings can be changed on a release build with
        ``adb push config.toml /sdcard/Android/data/<package>/files/``.
        An example copy is kept there to ``adb pull`` and edit.
        """
        target = Path(self.user_data_dir) / "config.toml"
        if not target.exists() and BUNDLED_CONFIG.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BUNDLED_CONFIG, target)
        os.environ["TUI_OS_CONFIG"] = str(target)
        self._debug_input = False

        external = crash_log_path(self).parent
        os.environ["TUI_OS_CONFIG_OVERRIDE"] = str(external / OVERRIDE_NAME)
        example = external / (OVERRIDE_NAME + ".example")
        try:
            external.mkdir(parents=True, exist_ok=True)
            if BUNDLED_CONFIG.exists():
                example.write_text(OVERRIDE_HEADER + BUNDLED_CONFIG.read_text())
        except OSError:
            pass  # the override is optional
        try:
            from ..config import Settings

            self._debug_input = Settings.load().debug_input
        except Exception:  # noqa: BLE001 - a bad config shows on the crash screen later
            pass

    # ------------------------------------------------------------ keyboard
    def _trace(self, msg: str) -> None:
        """debug_input = true: log every input event to logcat (tag "python")."""
        if self._debug_input:
            import time

            print(f"[input {time.monotonic():.3f}] {msg}")

    def _lock_keyboard(self) -> None:
        """Hold the system keyboard so SDL keeps delivering key/text events.

        With the Tab Ultra C keyboard cover attached Android keeps the soft
        keyboard hidden; if anything releases our claim we re-take it.
        """
        if self._keyboard is None:
            self._keyboard = Window.request_keyboard(self._keyboard_released, self.view, "text")

    def _keyboard_released(self) -> None:
        self._trace("keyboard released (re-requesting in 0.2 s)")
        self._keyboard = None
        Clock.schedule_once(lambda _dt: self._lock_keyboard(), 0.2)

    def _swallow_back(self, _window, key, *_args) -> bool:
        return key == 27  # never let BACK/Esc close the activity

    def _on_key_down(self, _window, keycode, _scancode, codepoint, modifiers) -> bool:
        self._trace(f"key_down keycode={keycode} scancode={_scancode} codepoint={codepoint!r} mods={modifiers}")
        action = translate_keydown(keycode, codepoint, modifiers or [])
        if isinstance(action, NamedKey) and action.key in PASTE_KEYS:
            self._cancel_pending()
            paste_clipboard(self.bridge)
            return True
        if isinstance(action, NamedKey):
            self._cancel_pending()
            # A Ctrl/Alt chord on a printable key must not also type that key
            # if the platform sends a text event for it (e.g. Ctrl+1 -> "1").
            base = action.key.rsplit("+", 1)[-1]
            base = " " if base == "space" else base
            self._swallow_text = (base, Clock.get_time()) if len(base) == 1 and "+" in action.key else None
            self.bridge.send_key(action.key, action.character)
            return True
        if isinstance(action, CharKey):
            # Text comes from layout-aware text-input events; the key itself is
            # only typed if no text event follows (see CharFallback).
            self._cancel_pending()
            if self._chars.key_down(action):
                self._pending_ev = Clock.schedule_once(self._flush_pending, TEXT_FALLBACK_DELAY)
            return True
        return False

    def _on_textinput(self, _window, text: str) -> None:
        self._trace(f"textinput {text!r}")
        swallow, self._swallow_text = self._swallow_text, None
        if swallow and text.lower() == swallow[0] and Clock.get_time() - swallow[1] < 0.3:
            self._trace(f"textinput {text!r} swallowed (part of a key chord)")
            return
        self._cancel_pending()
        self._chars.text_input()
        self.bridge.send_text(text)

    def _cancel_pending(self) -> None:
        if self._pending_ev is not None:
            self._pending_ev.cancel()
        self._pending_ev = None
        self._chars.cancel()

    def _flush_pending(self, _dt) -> None:
        self._pending_ev = None
        char = self._chars.flush()
        if char is not None:
            self._trace(f"fallback types {char!r} (no text event)")
            self.bridge.send_text(char)


def crash_log_path(app: App | None = None) -> Path:
    """Where crash.log goes.

    On Android: the app's external files dir
    (/sdcard/Android/data/org.olares.olares_workspace_tui/files/), readable with
    ``adb pull`` or the Boox file manager. Release builds aren't debuggable,
    so ``run-as`` can't reach private storage. Elsewhere: the user data dir.
    """
    if platform == "android":
        try:
            from jnius import autoclass  # type: ignore[import-not-found]

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            ext = activity.getExternalFilesDir(None)
            if ext is not None:
                return Path(ext.getAbsolutePath()) / "crash.log"
        except Exception:  # noqa: BLE001 - fall back to private storage
            pass
    base = Path(app.user_data_dir) if app is not None else Path(os.environ.get("ANDROID_PRIVATE", "."))
    return base / "crash.log"


def show_crash(app: App, text: str) -> None:
    """Replace the UI with the traceback (E-ink palette), write crash.log, log it."""
    from kivy.uix.label import Label

    print(text)  # logcat, tag "python"
    path = crash_log_path(app)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError:
        pass
    lines = text.rstrip().splitlines()
    tail = "\n".join(lines[-40:])  # the end of a traceback is what matters
    label = Label(
        text=f"TUI-OS crashed. Full traceback: {path}\nBACK / Esc closes the app.\n\n{tail}",
        font_name=str(FONT_REGULAR) if FONT_REGULAR.exists() else "Roboto",
        font_size=f"{max(12, int(Window.height / 60))}px",
        color=(1, 1, 1, 1),
        halign="left",
        valign="top",
        padding=(24, 24),
    )
    label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
    Window.unbind(on_key_down=app._on_key_down, on_textinput=app._on_textinput)
    Window.unbind(on_keyboard=app._swallow_back)
    Window.bind(on_keyboard=lambda _w, key, *_a: (app.stop(), True)[1] if key == 27 else False)
    Window.remove_widget(app.root)
    app.root = label
    Window.add_widget(label)
    label.size = Window.size


def _android_host(bridge: TerminalBridge, view: TerminalView):
    """The native WebView host on Android; None elsewhere (text browser)."""
    if platform != "android":
        return None
    try:
        from .android_host import AndroidHost

        return AndroidHost(bridge, view)
    except Exception:  # noqa: BLE001 - fall back to the text browser, log why
        import traceback

        traceback.print_exc()
        return None


def _enter_immersive_mode() -> None:
    """Hide status and navigation bars (sticky immersive) on Android."""
    if platform != "android":
        return
    try:
        from android.runnable import run_on_ui_thread  # type: ignore[import-not-found]
        from jnius import autoclass  # type: ignore[import-not-found]
    except ImportError:
        return

    @run_on_ui_thread
    def apply() -> None:
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        View = autoclass("android.view.View")
        activity.getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            | View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
        )

    apply()


def main() -> None:
    OlaresWorkspaceApp().run()
