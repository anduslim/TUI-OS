"""Run a Textual app inside a host that isn't a terminal (Kivy on Android).

Textual normally talks to a TTY: it writes ANSI escape sequences to stdout
and parses key bytes from stdin. An Android app process has no TTY, so this
module puts a small virtual screen in the middle:

    Textual App ──compositor updates──▶ CellGrid ──▶ styled runs per row
         ▲                                              │
         │ events.Key / Resize / Mouse                  ▼ dirty rows
    TerminalBridge ◀── keys / taps / size ──── host UI (Kivy TerminalView)

* The Textual app runs on its own thread with its own asyncio loop, exactly
  as it would in a terminal; the host's UI thread never blocks on it.
* Screen updates skip the terminal round trip: the app's ``_display`` is
  wrapped so the compositor's segments go straight into a
  :class:`~tui_os.host.cells.CellGrid` instead of being encoded as ANSI and
  parsed back (that round trip used to be half the app thread's CPU). The
  app thread also turns the changed rows into styled runs. The host polls
  :meth:`TerminalBridge.take_dirty_rows`, which only swaps a dict under a
  short lock, so the UI thread never waits for terminal parsing. Rows whose
  content did not actually change are dropped, which suits E-ink.
* Input is posted onto the app's loop with ``call_soon_threadsafe``.

Nothing here imports Kivy, so the whole pipeline is testable on a desktop.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import threading
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

# Must be set before Textual is imported: emit 24-bit colour so the exact
# design tokens reach the screen instead of a 16-colour approximation.
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
os.environ.setdefault("COLORTERM", "truecolor")

from textual import events  # noqa: E402
from textual._compositor import CompositorUpdate  # noqa: E402
from textual._xterm_parser import XTermParser  # noqa: E402
from textual.driver import Driver  # noqa: E402
from textual.geometry import Size  # noqa: E402
from textual.keys import _character_to_key  # noqa: E402

from .cells import DEFAULT_BG, DEFAULT_FG, CellGrid, Run  # noqa: E402,F401 - Run re-exported

if TYPE_CHECKING:
    from textual.app import App

def _swallowed_error(app: "App[Any]") -> str | None:
    """The traceback of an exception Textual caught and exited on.

    Textual doesn't re-raise errors from compose/mount/handlers: it renders
    the traceback to stderr (invisible in an APK) and returns from ``run()``
    with return code 1. Recover the exception so the host can show it.
    """
    if not app.return_code:
        return None
    exc = getattr(app, "_exception", None)
    if isinstance(exc, BaseException):
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"The app exited with return code {app.return_code}."


_OSC52 = re.compile(r"\x1b\]52;[^;]*;([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)")


def clipboard_texts(data: str) -> list[str]:
    """Texts in OSC 52 "set clipboard" sequences (App.copy_to_clipboard)."""
    texts = []
    for payload in _OSC52.findall(data):
        try:
            texts.append(base64.b64decode(payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
    return texts


class EmbeddedDriver(Driver):
    """Textual driver whose 'terminal' is a :class:`TerminalBridge`.

    Bound to a bridge with :meth:`TerminalBridge.driver_class`.
    """

    bridge: "TerminalBridge"

    def write(self, data: str) -> None:
        # Screen content arrives through the wrapped App._display (see
        # TerminalBridge._hook_display); what is left here is terminal
        # housekeeping (mouse modes, sync markers, cursor), which has no
        # meaning without a terminal, and App.copy_to_clipboard's OSC 52,
        # which goes to the host's clipboard.
        if "\x1b]52;" in data:
            for text in clipboard_texts(data):
                self.bridge.set_clipboard(text)

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
        #: Guards the cell grid (written on the app thread).
        self._lock = threading.Lock()
        #: Guards ``_pending`` only; the host's UI thread takes just this one.
        self._out_lock = threading.Lock()
        #: Rows ready for the host: y -> runs. Newer frames overwrite older.
        self._pending: dict[int, list[Run]] = {}
        #: Last runs published per row, to drop rows that didn't change.
        self._published: dict[int, list[Run]] = {}
        self._grid = CellGrid(cols, rows)
        self._driver: EmbeddedDriver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._parser = XTermParser(False)
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.app: "App[Any] | None" = None
        self.error: str | None = None
        #: Set by the host: puts text on the system clipboard (any thread).
        self.on_clipboard: Callable[[str], None] | None = None

    # ----------------------------------------------------------- lifecycle
    @property
    def size(self) -> tuple[int, int]:
        with self._lock:
            return self._grid.cols, self._grid.rows

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
            self._hook_display(self.app)
            self.app.run()
            self.error = self.error or _swallowed_error(self.app)
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
    def _hook_display(self, app: "App[Any]") -> None:
        """Route compositor updates into the cell grid instead of ANSI text."""
        original = app._display

        def display(screen, renderable) -> None:
            if not isinstance(renderable, CompositorUpdate):
                original(screen, renderable)
                return
            if getattr(app, "_batch_count", 0) or getattr(app, "_closed", False):
                return
            try:
                with self._lock:
                    self._grid.apply(renderable)
                    self._collect(force=False)
            except Exception as error:  # noqa: BLE001 - report like Textual would
                app._handle_exception(error)

        app._display = display  # type: ignore[method-assign]

    def _collect(self, *, force: bool) -> None:
        """Turn the grid's dirty rows into runs and queue them for the host.

        Called with ``_lock`` held, on whichever thread changed the screen
        (normally the app thread), so the host's UI thread never pays for it.
        """
        grid = self._grid
        rows = [y for y in grid.dirty if y < grid.rows]
        grid.dirty.clear()
        if not rows:
            return
        published = self._published
        ready: dict[int, list[Run]] = {}
        for y in rows:
            runs = grid.row_runs(y)
            if force or published.get(y) != runs:
                published[y] = runs
                ready[y] = runs
        if ready:
            with self._out_lock:
                self._pending.update(ready)

    def take_dirty_rows(self) -> list[tuple[int, list[Run]]]:
        """Rows changed since the last call, as styled runs. Clears the set.

        Cheap: the runs were built on the app thread; this only swaps a dict.
        """
        with self._out_lock:
            pending, self._pending = self._pending, {}
        lines = self._grid.rows
        return sorted(((y, r) for y, r in pending.items() if y < lines), key=lambda item: item[0])

    def has_dirty_rows(self) -> bool:
        return bool(self._pending)

    def mark_all_dirty(self) -> None:
        """Queue every row for repainting (after a host redraw or resume)."""
        with self._lock:
            self._grid.dirty.update(range(self._grid.rows))
            self._collect(force=True)

    def text_lines(self) -> list[str]:
        """Plain-text screen contents (for tests and logs)."""
        with self._lock:
            return self._grid.text_lines()

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

    def set_clipboard(self, text: str) -> None:
        if self.on_clipboard is not None:
            self.on_clipboard(text)

    def send_key(self, key: str, character: str | None = None) -> bool:
        """Send a named key, e.g. ``"ctrl+b"``, ``"f2"``, ``"pagedown"``."""
        return self._dispatch(events.Key(key, character))

    def send_text(self, text: str) -> None:
        for ch in text:
            self.send_key(_character_to_key(ch), ch)

    def send_paste(self, text: str) -> bool:
        """Deliver clipboard text as one Textual Paste event (bracketed paste).

        Input fields, the compose editor and the SSH terminal all handle
        Paste, so a pasted password arrives whole instead of as keystrokes
        that could trigger bindings.
        """
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return False
        return self._dispatch(events.Paste(text))

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
            if (cols, rows) == (self._grid.cols, self._grid.rows):
                return
            self._grid.resize(cols, rows)
            self._published.clear()
            self._collect(force=True)
        size = Size(cols, rows)
        self._dispatch(events.Resize(size, size))
