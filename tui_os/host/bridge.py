"""Run a Textual app inside a host that isn't a terminal (Kivy on Android).

Textual normally talks to a TTY: it writes ANSI escape sequences to stdout
and parses key bytes from stdin. An Android app process has no TTY, so this
module puts a small virtual terminal in the middle:

    Textual App ──write(ANSI)──▶ EmbeddedDriver ──▶ pyte.Screen (cell grid)
         ▲                                              │
         │ events.Key / Resize / Mouse                  ▼ dirty rows
    TerminalBridge ◀── keys / taps / size ──── host UI (Kivy TerminalView)

* The Textual app runs on its own thread with its own asyncio loop, exactly
  as it would in a terminal; the host's UI thread never blocks on it.
* Output is fed into a ``pyte`` screen under a lock. The host polls
  :meth:`TerminalBridge.take_dirty_rows` and redraws only rows that changed,
  which suits E-ink.
* Input is posted onto the app's loop with ``call_soon_threadsafe``.

Nothing here imports Kivy, so the whole pipeline is testable on a desktop.
"""

from __future__ import annotations

import asyncio
import os
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# Must be set before Textual is imported: emit 24-bit colour so the exact
# design tokens reach the screen instead of a 16-colour approximation.
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
os.environ.setdefault("COLORTERM", "truecolor")

import pyte  # noqa: E402
from textual import events  # noqa: E402
from textual._xterm_parser import XTermParser  # noqa: E402
from textual.driver import Driver  # noqa: E402
from textual.geometry import Size  # noqa: E402
from textual.keys import _character_to_key  # noqa: E402

if TYPE_CHECKING:
    from textual.app import App

DEFAULT_FG = "FFFFFF"
DEFAULT_BG = "000000"

# pyte reports SGR 30-37/90-97 colours by name. The app emits truecolor, so
# these only appear if something prints raw ANSI; map them to the six tokens.
_NAMED = {
    "black": "000000", "red": "FF0000", "green": "00FF00", "brown": "FFFF00",
    "yellow": "FFFF00", "blue": "0000FF", "magenta": "FF0000", "cyan": "00FF00",
    "white": "FFFFFF", "brightblack": "000000", "brightred": "FF0000",
    "brightgreen": "00FF00", "brightbrown": "FFFF00", "brightyellow": "FFFF00",
    "brightblue": "0000FF", "brightmagenta": "FF0000", "brightcyan": "00FF00",
    "brightwhite": "FFFFFF",
}


def _colour(value: str, default: str) -> str:
    if not value or value == "default":
        return default
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.upper()
    return _NAMED.get(value, default)


@dataclass(frozen=True)
class Run:
    """A horizontal span of cells sharing one style."""

    col: int
    text: str
    fg: str  # RRGGBB
    bg: str  # RRGGBB
    bold: bool = False
    underline: bool = False
    italic: bool = False


class EmbeddedDriver(Driver):
    """Textual driver whose 'terminal' is a :class:`TerminalBridge`.

    Bound to a bridge with :meth:`TerminalBridge.driver_class`.
    """

    bridge: "TerminalBridge"

    def write(self, data: str) -> None:
        self.bridge._feed_output(data)

    def start_application_mode(self) -> None:
        loop = asyncio.get_running_loop()
        self.bridge._attach(self, loop)
        cols, rows = self.bridge.size
        size = Size(cols, rows)
        asyncio.run_coroutine_threadsafe(
            self._app._post_message(events.Resize(size, size)), loop=loop
        )

    def disable_input(self) -> None:
        self.bridge._detach()

    def stop_application_mode(self) -> None:
        self.bridge._detach()


class TerminalBridge:
    def __init__(
        self,
        app_factory: Callable[[type[Driver]], "App[Any]"],
        *,
        cols: int = 120,
        rows: int = 40,
        on_exit: Callable[["TerminalBridge"], None] | None = None,
    ) -> None:
        self._app_factory = app_factory
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.Stream(self._screen)
        self._driver: EmbeddedDriver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._parser = XTermParser(False)
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.app: "App[Any] | None" = None
        self.error: str | None = None

    # ----------------------------------------------------------- lifecycle
    @property
    def size(self) -> tuple[int, int]:
        with self._lock:
            return self._screen.columns, self._screen.lines

    def driver_class(self) -> type[EmbeddedDriver]:
        return type("BoundEmbeddedDriver", (EmbeddedDriver,), {"bridge": self})

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("bridge already started")
        self._thread = threading.Thread(target=self._run, name="textual-app", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.app = self._app_factory(self.driver_class())
            self.app.run()
        except BaseException:  # noqa: BLE001 - surface any crash to the host
            self.error = traceback.format_exc()
        finally:
            self._detach()
            self.finished.set()
            if self._on_exit is not None:
                self._on_exit(self)

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the app to exit and wait for its thread."""
        app, loop = self.app, self._loop
        if app is not None and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(app.exit)
        if self._thread is not None:
            self._thread.join(timeout)

    def _attach(self, driver: EmbeddedDriver, loop: asyncio.AbstractEventLoop) -> None:
        self._driver, self._loop = driver, loop
        self.ready.set()

    def _detach(self) -> None:
        self._driver = None

    # -------------------------------------------------------------- output
    def _feed_output(self, data: str) -> None:
        with self._lock:
            self._stream.feed(data)

    def take_dirty_rows(self) -> list[tuple[int, list[Run]]]:
        """Rows changed since the last call, as styled runs. Clears the set."""
        with self._lock:
            rows = sorted(y for y in self._screen.dirty if y < self._screen.lines)
            self._screen.dirty.clear()
            return [(y, self._row_runs(y)) for y in rows]

    def mark_all_dirty(self) -> None:
        with self._lock:
            self._screen.dirty.update(range(self._screen.lines))

    def text_lines(self) -> list[str]:
        """Plain-text screen contents (for tests and logs)."""
        with self._lock:
            return list(self._screen.display)

    def _row_runs(self, y: int) -> list[Run]:
        line = self._screen.buffer[y]
        runs: list[Run] = []
        cur_key: tuple[Any, ...] | None = None
        start = 0
        chars: list[str] = []
        for x in range(self._screen.columns):
            ch = line[x]
            if ch.data == "":  # right half of a wide character
                continue
            fg = _colour(ch.fg, DEFAULT_FG)
            bg = _colour(ch.bg, DEFAULT_BG)
            if ch.reverse:
                fg, bg = bg, fg
            key = (fg, bg, ch.bold, ch.underscore, ch.italics)
            if key != cur_key:
                if cur_key is not None:
                    runs.append(Run(start, "".join(chars), *cur_key))
                cur_key, start, chars = key, x, []
            chars.append(ch.data)
        if cur_key is not None:
            runs.append(Run(start, "".join(chars), *cur_key))
        return runs

    # --------------------------------------------------------------- input
    def _dispatch(self, event: events.Event) -> bool:
        driver, loop = self._driver, self._loop
        if driver is None or loop is None or loop.is_closed():
            return False
        loop.call_soon_threadsafe(driver.process_message, event)
        return True

    def call_in_app(self, callback: Callable[["App[Any]"], None]) -> bool:
        """Run ``callback(app)`` on the app's event loop (thread-safe)."""
        app, loop = self.app, self._loop
        if app is None or loop is None or loop.is_closed():
            return False
        loop.call_soon_threadsafe(callback, app)
        return True

    def send_key(self, key: str, character: str | None = None) -> bool:
        """Send a named key, e.g. ``"ctrl+b"``, ``"f2"``, ``"pagedown"``."""
        return self._dispatch(events.Key(key, character))

    def send_text(self, text: str) -> None:
        for ch in text:
            self.send_key(_character_to_key(ch), ch)

    def tap(self, col: int, row: int) -> None:
        """Left click at a cell (zero-based), via SGR mouse sequences.

        A pointer move comes first, as from a real terminal: Textual only
        turns down+up into a Click once it knows which widget is under the
        pointer.
        """
        x, y = col + 1, row + 1
        for seq in (f"\x1b[<35;{x};{y}M", f"\x1b[<0;{x};{y}M", f"\x1b[<0;{x};{y}m"):
            for event in self._parser.feed(seq):
                self._dispatch(event)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(20, cols), max(8, rows)
        with self._lock:
            if (cols, rows) == (self._screen.columns, self._screen.lines):
                return
            self._screen.resize(rows, cols)
            self._screen.dirty.update(range(rows))
        size = Size(cols, rows)
        self._dispatch(events.Resize(size, size))
