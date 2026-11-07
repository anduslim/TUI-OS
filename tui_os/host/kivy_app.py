"""Kivy host for the Android APK.

The whole window is one :class:`TerminalView` that paints the
:class:`~tui_os.host.bridge.TerminalBridge` cell grid and feeds it the
hardware keyboard and taps. No Android terminal or shell is involved: the
Textual app runs on a thread inside this process.

E-ink behaviour:
* Rows are repainted only when the bridge reports them dirty. Kivy only
  redraws the GL surface when canvas instructions change, so an idle TUI
  leaves the panel untouched.
* A vertical swipe sends PgUp/PgDn (page-based navigation) instead of
  scrolling smoothly.
"""

from __future__ import annotations

import os
import shutil
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
from kivy.graphics import Color, InstructionGroup, Rectangle  # noqa: E402
from kivy.uix.widget import Widget  # noqa: E402
from kivy.utils import platform  # noqa: E402

from .bridge import DEFAULT_BG, Run, TerminalBridge  # noqa: E402
from .glyphs import DRAWN, cell_rects  # noqa: E402
from .grid import GRID_COLS, GRID_ROWS, GridGeometry  # noqa: E402
from .keymap import CharKey, NamedKey, translate_keydown  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "assets" / "fonts"
FONT_REGULAR = FONT_DIR / "JetBrainsMonoNL-Regular.ttf"
FONT_BOLD = FONT_DIR / "JetBrainsMonoNL-Bold.ttf"
BUNDLED_CONFIG = ROOT / "android" / "config.toml"

REFRESH_HZ = 20
SWIPE_ROWS = 3
TEXT_FALLBACK_DELAY = 0.06


def _rgb(hex6: str) -> tuple[float, float, float, float]:
    return (int(hex6[0:2], 16) / 255, int(hex6[2:4], 16) / 255, int(hex6[4:6], 16) / 255, 1.0)


class TerminalView(Widget):
    """Paints the bridge's fixed 147×46 cell grid, scaled to fit the window."""

    def __init__(self, bridge: TerminalBridge, **kw) -> None:
        super().__init__(**kw)
        self.bridge = bridge
        self.grid = GridGeometry.fit(1, 1)
        self._fonts = {False: str(FONT_REGULAR), True: str(FONT_BOLD)}
        self._rows: dict[int, InstructionGroup] = {}
        self._textures: dict[tuple[str, str, bool], object] = {}
        self._touch_start: tuple[float, float] | None = None
        with self.canvas.before:
            Color(*_rgb(DEFAULT_BG))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._on_geometry, size=self._on_geometry)
        Clock.schedule_interval(self._poll, 1 / REFRESH_HZ)

    # ------------------------------------------------------------ geometry
    def grid_size(self) -> tuple[int, int]:
        return GRID_COLS, GRID_ROWS

    def _on_geometry(self, *_a) -> None:
        self._bg.pos, self._bg.size = self.pos, self.size
        grid = GridGeometry.fit(self.width, self.height)
        if grid.font_px != self.grid.font_px:
            self._textures.clear()
        self.grid = grid
        self.bridge.resize(GRID_COLS, GRID_ROWS)
        self.full_redraw()

    def full_redraw(self) -> None:
        for group in self._rows.values():
            self.canvas.remove(group)
        self._rows.clear()
        self.bridge.mark_all_dirty()

    def cell_at(self, x: float, y: float) -> tuple[int, int]:
        return self.grid.cell_at(x - self.x, self.top - y)

    # ------------------------------------------------------------- painting
    def _poll(self, _dt: float) -> None:
        for y, runs in self.bridge.take_dirty_rows():
            self._paint_row(y, runs)

    def _texture(self, text: str, fg: str, bold: bool):
        key = (text, fg, bold)
        tex = self._textures.get(key)
        if tex is None:
            label = CoreLabel(
                text=text, font_size=self.grid.font_px, font_name=self._fonts[bold], color=_rgb(fg)
            )
            label.refresh()
            tex = label.texture
            if len(self._textures) > 4096:
                self._textures.clear()
            self._textures[key] = tex
        return tex

    def _rect(self, group: InstructionGroup, x: int, y_down: int, w: int, h: int) -> None:
        """Add a rectangle given in top-down pixel coordinates."""
        group.add(Rectangle(pos=(self.x + x, self.top - y_down - h), size=(w, h)))

    def _paint_row(self, row: int, runs: list[Run]) -> None:
        old = self._rows.pop(row, None)
        if old is not None:
            self.canvas.remove(old)
        g = self.grid
        group = InstructionGroup()
        y0, y1 = g.row_edge(row), g.row_edge(row + 1)
        for run in runs:
            x0, x1 = g.col_edge(run.col), g.col_edge(run.col + len(run.text))
            if run.bg != DEFAULT_BG:
                group.add(Color(*_rgb(run.bg)))
                self._rect(group, x0, y0, x1 - x0, y1 - y0)
            fg = _rgb(run.fg)
            # Split the run: box/block glyphs as rectangles, the rest as text.
            seg_start, seg = run.col, []
            for i, ch in enumerate(run.text + "\0"):
                col = run.col + i
                if ch in DRAWN or ch == "\0":
                    if seg and "".join(seg).strip():
                        self._paint_text(group, "".join(seg), seg_start, y0, y1, run)
                    seg, seg_start = [], col + 1
                    if ch in DRAWN:
                        group.add(Color(*fg))
                        cx0, cx1 = g.col_edge(col), g.col_edge(col + 1)
                        for r in cell_rects(ch, cx0, y0, cx1, y1, g.scale):
                            self._rect(group, *r)
                else:
                    seg.append(ch)
            if run.underline:
                group.add(Color(*fg))
                t = max(1, round(g.scale))
                self._rect(group, x0, y1 - 2 * t, x1 - x0, t)
        self.canvas.add(group)
        self._rows[row] = group

    def _paint_text(self, group, text: str, col: int, y0: int, y1: int, run: Run) -> None:
        tex = self._texture(text, run.fg, run.bold)
        group.add(Color(1, 1, 1, 1))
        x = self.x + self.grid.col_edge(col)
        # Centre vertically in the cell; integer position keeps glyphs sharp.
        y_down = y0 + ((y1 - y0) - tex.height) // 2
        group.add(Rectangle(texture=tex, pos=(x, self.top - y_down - tex.height), size=tex.size))

    # ---------------------------------------------------------------- touch
    def on_touch_down(self, touch) -> bool:
        if self.collide_point(*touch.pos):
            self._touch_start = touch.pos
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
        return True


class OlaresWorkspaceApp(App):
    title = "Olares Workspace TUI"

    def build(self) -> TerminalView:
        Window.clearcolor = _rgb(DEFAULT_BG)
        Window.softinput_mode = "below_target"
        self._prepare_config()
        self.bridge = TerminalBridge(self._make_tui, on_exit=self._tui_exited)
        self.view = TerminalView(self.bridge)
        self._pending_char: CharKey | None = None
        self._pending_ev = None
        self._keyboard = None
        Window.bind(on_key_down=self._on_key_down, on_textinput=self._on_textinput)
        Window.bind(on_keyboard=self._swallow_back)
        return self.view

    # ----------------------------------------------------------- lifecycle
    def on_start(self) -> None:
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

    def _make_tui(self, driver_class):
        from ..app import TuiOS
        from ..config import Settings

        return TuiOS(Settings.load(), driver_class=driver_class)

    def _tui_exited(self, bridge: TerminalBridge) -> None:
        if bridge.error:
            print(bridge.error)  # goes to logcat ("python" tag)
        Clock.schedule_once(lambda _dt: self.stop(), 0)

    def _prepare_config(self) -> None:
        """First launch: copy the bundled config.toml to private storage."""
        target = Path(self.user_data_dir) / "config.toml"
        if not target.exists() and BUNDLED_CONFIG.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BUNDLED_CONFIG, target)
        os.environ["TUI_OS_CONFIG"] = str(target)

    # ------------------------------------------------------------ keyboard
    def _lock_keyboard(self) -> None:
        """Hold the system keyboard so SDL keeps delivering key/text events.

        With the Tab Ultra C keyboard cover attached Android keeps the soft
        keyboard hidden; if anything releases our claim we re-take it.
        """
        if self._keyboard is None:
            self._keyboard = Window.request_keyboard(self._keyboard_released, self.view, "text")

    def _keyboard_released(self) -> None:
        self._keyboard = None
        Clock.schedule_once(lambda _dt: self._lock_keyboard(), 0.2)

    def _swallow_back(self, _window, key, *_args) -> bool:
        return key == 27  # never let BACK/Esc close the activity

    def _on_key_down(self, _window, keycode, _scancode, codepoint, modifiers) -> bool:
        action = translate_keydown(keycode, codepoint, modifiers or [])
        if isinstance(action, NamedKey):
            self._cancel_pending()
            self.bridge.send_key(action.key, action.character)
            return True
        if isinstance(action, CharKey):
            # Wait briefly for the layout-aware text event; fall back if none.
            self._cancel_pending()
            self._pending_char = action
            self._pending_ev = Clock.schedule_once(self._flush_pending, TEXT_FALLBACK_DELAY)
            return True
        return False

    def _on_textinput(self, _window, text: str) -> None:
        self._cancel_pending()
        self.bridge.send_text(text)

    def _cancel_pending(self) -> None:
        if self._pending_ev is not None:
            self._pending_ev.cancel()
        self._pending_ev = None
        self._pending_char = None

    def _flush_pending(self, _dt) -> None:
        if self._pending_char is not None:
            self.bridge.send_text(self._pending_char.character)
        self._pending_char = None
        self._pending_ev = None


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
