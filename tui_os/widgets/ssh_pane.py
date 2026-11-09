"""A tab that runs one program in a remote PTY over SSH (Shell, K9s).

The terminal fills the bordered panel exactly. Its cell size is sent to the
remote PTY on connect and on every resize (e.g. Ctrl+B widening the
workspace), so the remote program always lays out inside the grid.

Subclasses say what runs and how the panel is labelled; see
:class:`tui_os.widgets.shell.ShellPane` and :class:`tui_os.widgets.k9s.K9sPane`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.css.query import NoMatches
from textual.widgets import Static, TabbedContent, TabPane

from ..config import Settings
from ..services.ssh import SshTarget, SshTerminalSession, ensure_client_key
from .term_emulator import TerminalEmulator

STYLE_BAR = "bold #FFFFFF on #0000FF"


class TerminalSession(Protocol):
    async def start(self, cols: int, rows: int) -> None: ...
    def write(self, data: str) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    async def close(self) -> None: ...


SessionFactory = Callable[
    [SshTarget, Callable[[str], None], Callable[[int | None, str | None], None]], TerminalSession
]


class SshIdle(Static, can_focus=True):
    """Shown while not connected: what will happen, the device key, errors."""

    BINDINGS = [Binding("enter", "connect", "Connect")]

    def action_connect(self) -> None:
        self.post_message(SshPane.ConnectRequested())


class SshPane(Vertical):
    #: Status bar name, e.g. "SHELL".
    TITLE = "SSH"
    #: Border title of the terminal frame.
    FRAME_TITLE = "ssh"
    #: Keys the app keeps while the remote program has focus.
    RESERVED: frozenset[str] = TerminalEmulator.RESERVED

    class ConnectRequested(Message):
        """Enter on the idle screen."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: SessionFactory | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.settings = settings
        self.target = self.make_target(settings)
        self._factory: SessionFactory = session_factory or SshTerminalSession
        self.session: TerminalSession | None = None
        self.state = "idle"
        self.public_key = ""
        self.error: str | None = None
        self._activated = False
        self._closing = False

    # ------------------------------------------------------- subclass hooks
    def make_target(self, settings: Settings) -> SshTarget:
        raise NotImplementedError

    @property
    def autoconnect(self) -> bool:
        return True

    def help_text(self, connected: bool) -> str:
        return " Enter connect   tap a tab to switch   ^b telemetry"

    def describe(self, text: Text) -> None:
        """Idle screen: heading and what Enter will do."""

    def ended_text(self) -> str:
        return "session ended"

    # ---------------------------------------------------------------- view
    def compose(self) -> ComposeResult:
        yield Static("", classes="bar ssh-status")
        with Vertical(classes="ssh-frame"):
            yield SshIdle("", classes="ssh-idle")
            term = TerminalEmulator(on_input=self._send, classes="ssh-term")
            term.RESERVED = self.RESERVED
            yield term
        yield Static("", classes="bar ssh-help")

    def on_mount(self) -> None:
        self.query_one(".ssh-frame").border_title = self.FRAME_TITLE
        self.query_one(TerminalEmulator).display = False
        try:
            self.public_key = ensure_client_key(self.target.key_path)
        except ImportError as exc:  # native cryptography failed to load on this device
            self.public_key = "(unavailable)"
            self.state, self.error = "error", f"SSH is unavailable on this device: {exc}"
        except OSError as exc:
            self.public_key = f"(could not create key at {self.target.key_path}: {exc})"
        self._render_state()

    @property
    def terminal(self) -> TerminalEmulator:
        return self.query_one(TerminalEmulator)

    @property
    def focus_target(self):
        return self.terminal if self.state == "connected" else self.query_one(SshIdle)

    def _render_state(self) -> None:
        t = self.target
        connected = self.state == "connected"
        cols, rows = self.terminal.grid_size
        status = {
            "idle": "not connected",
            "connecting": "connecting…",
            "connected": f"connected · {cols}×{rows}",
            "ended": self.ended_text(),
            "error": "error",
        }[self.state]
        self.query_one(".ssh-status", Static).update(
            Text(f" {self.TITLE} · {t.label} · {status}".ljust(300), STYLE_BAR)
        )
        self.query_one(".ssh-help", Static).update(Text(self.help_text(connected).ljust(300), STYLE_BAR))
        self.terminal.display = connected
        idle = self.query_one(SshIdle)
        idle.display = not connected
        if not connected:
            idle.update(self._idle_text())

    def _idle_text(self) -> Text:
        t = self.target
        text = Text()
        self.describe(text)
        if self.state == "connecting":
            text.append(f"Connecting to {t.label} …\n", "bold #00FF00")
        elif self.state == "error" and self.error:
            text.append(" error ", "bold #FFFFFF on #FF0000")
            text.append(f" {self.error}\n", "bold #FF0000")
        elif self.state == "ended":
            text.append(f"{self.ended_text().capitalize()}{f' ({self.error})' if self.error else ''}.\n",
                        "bold #FFFF00")
        text.append("\n")
        text.append(" Enter ", "bold #FFFFFF on #0000FF")
        text.append(f"  connect: ssh {t.label}", "#FFFFFF")
        if t.command.strip():
            text.append(", then run ", "#FFFFFF")
            text.append(t.command, "bold #00FF00")
        text.append("\n\n")
        text.append("Device key", "bold #00FF00")
        text.append(" (add to ~/.ssh/authorized_keys on the node):\n", "#FFFFFF")
        text.append(f"{self.public_key}\n\n", "#FFFFFF")
        text.append("Using Tailscale SSH on the node? No key needed.\n", "#FFFFFF")
        text.append(f"Host key: trusted on first use, pinned in {t.known_hosts_path}\n", "#FFFFFF")
        self.idle_footer(text)
        return text

    def idle_footer(self, text: Text) -> None:
        """Extra lines at the end of the idle screen."""

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        self._render_state()
        self.app.call_after_refresh(self._focus_if_shown)

    def _focus_if_shown(self) -> None:
        """Focus our terminal or idle screen, but only while our tab is shown.

        A connect that finishes (or fails) after the user moved to another tab
        must not grab focus: TabbedContent would switch back to this tab.
        """
        if not self.is_mounted:
            return
        try:
            pane = self.query_ancestor(TabPane)
            tabs = self.query_ancestor(TabbedContent)
        except NoMatches:
            pane = tabs = None
        if pane is None or tabs.active == pane.id:
            self.focus_target.focus()

    # ----------------------------------------------------------- lifecycle
    def activate(self) -> None:
        """Called when the tab is shown; connects once if autoconnect is on."""
        if not self._activated:
            self._activated = True
            if self.autoconnect and self.state == "idle":
                self.connect()

    @on(ConnectRequested)
    def _connect_requested(self) -> None:
        if self.state != "connecting":
            self.connect()

    @work(exclusive=True, group="ssh", exit_on_error=False)
    async def connect(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.terminal.reset()
        self._set_state("connecting")
        # Show the terminal invisibly-sized first so we know its cell grid.
        self.terminal.display = True
        self.query_one(SshIdle).display = False
        for _ in range(40):
            await asyncio.sleep(0.025)
            if self.terminal.size.width > 0 and self.terminal.size.height > 0:
                break
        cols, rows = self.terminal.size.width or 80, self.terminal.size.height or 24
        session = self._factory(self.target, self.terminal.feed, self._exited)
        try:
            await session.start(cols, rows)
        except Exception as exc:  # noqa: BLE001 - shown in the panel
            self.session = None
            self._set_state("error", f"{type(exc).__name__}: {exc}")
            return
        self.session = session
        self._set_state("connected")

    def _exited(self, status: int | None, error: str | None) -> None:
        self.session = None
        if self.is_mounted and not self._closing:
            detail = error or (f"status {status}" if status not in (None, 0) else None)
            self._set_state("ended", detail)

    def _send(self, data: str) -> None:
        if self.session is not None:
            self.session.write(data)

    @on(TerminalEmulator.Resized)
    def _resized(self, event: TerminalEmulator.Resized) -> None:
        if self.session is not None:
            self.session.resize(event.cols, event.rows)
        if self.state == "connected":
            self._render_state()

    async def on_unmount(self) -> None:
        self._closing = True  # the session's exit callback must not repaint us
        if self.session is not None:
            await self.session.close()
