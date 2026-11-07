"""K9s (Cluster) tab: k9s running on the Olares One over SSH.

The terminal fills the bordered panel exactly. Its cell size is sent to the
remote PTY on connect and on every resize (e.g. Ctrl+B widening the
workspace), so k9s always lays out inside the grid without clipping.

Keyboard: every key goes to k9s (arrows, Esc, Enter, ``:`` commands, ``/``
filters, digits for namespace shortcuts, Ctrl combinations) except the
app's own F1–F5, Ctrl+B, Ctrl+R and Ctrl+Q. k9s's Ctrl+B page-up is also
on PgUp.
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
from textual.widgets import Static

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


def target_from_settings(settings: Settings) -> SshTarget:
    data = settings.data_dir
    return SshTarget(
        host=settings.resolved_k9s_host,
        username=settings.k9s_user,
        port=settings.k9s_port,
        command=settings.k9s_command,
        kubeconfig=settings.k9s_kubeconfig,
        refresh_rate=settings.k9s_refresh_rate,
        key_path=settings.resolved_ssh_key_path,
        known_hosts_path=data / "known_hosts",
    )


class K9sIdle(Static, can_focus=True):
    """Shown while not connected: what will happen, the device key, errors."""

    BINDINGS = [Binding("enter", "connect", "Connect")]

    def action_connect(self) -> None:
        self.post_message(K9sPane.ConnectRequested())


class K9sPane(Vertical):
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
        self.target = target_from_settings(settings)
        self._factory: SessionFactory = session_factory or SshTerminalSession
        self.session: TerminalSession | None = None
        self.state = "idle"
        self.public_key = ""
        self.error: str | None = None
        self._activated = False
        self._closing = False

    def compose(self) -> ComposeResult:
        yield Static("", id="k9s-status", classes="bar")
        with Vertical(id="k9s-frame"):
            yield K9sIdle("", id="k9s-idle")
            yield TerminalEmulator(on_input=self._send, id="k9s-term")
        yield Static("", id="k9s-help", classes="bar")

    def on_mount(self) -> None:
        self.query_one("#k9s-frame").border_title = "k9s"
        self.query_one(TerminalEmulator).display = False
        try:
            self.public_key = ensure_client_key(self.target.key_path)
        except OSError as exc:
            self.public_key = f"(could not create key at {self.target.key_path}: {exc})"
        self._render_state()

    # ---------------------------------------------------------------- view
    @property
    def terminal(self) -> TerminalEmulator:
        return self.query_one(TerminalEmulator)

    @property
    def focus_target(self):
        return self.terminal if self.state == "connected" else self.query_one(K9sIdle)

    def _render_state(self) -> None:
        t = self.target
        connected = self.state == "connected"
        cols, rows = self.terminal.grid_size
        status = {
            "idle": "not connected",
            "connecting": "connecting…",
            "connected": f"connected · {cols}×{rows}",
            "ended": "session ended",
            "error": "error",
        }[self.state]
        self.query_one("#k9s-status", Static).update(
            Text(f" K9S (CLUSTER) · {t.label} · {status}".ljust(300), STYLE_BAR)
        )
        help_text = (
            " keys → k9s: ↑↓←→ Enter Esc : / 0-9 ?   app: F1–F5 tabs  ^b telemetry  ^r redraw  ^q quit   ^c quits k9s"
            if connected
            else " Enter connect   F1–F5 tabs   ^b telemetry"
        )
        self.query_one("#k9s-help", Static).update(Text(help_text.ljust(300), STYLE_BAR))
        self.terminal.display = connected
        idle = self.query_one(K9sIdle)
        idle.display = not connected
        if not connected:
            idle.update(self._idle_text())

    def _idle_text(self) -> Text:
        t = self.target
        text = Text()
        text.append("k9s on the Olares One\n\n", "bold #FFFFFF")
        if self.state == "connecting":
            text.append(f"Connecting to {t.label} …\n", "bold #00FF00")
        elif self.state == "error" and self.error:
            text.append(" error ", "bold #FFFFFF on #FF0000")
            text.append(f" {self.error}\n", "bold #FF0000")
        elif self.state == "ended":
            text.append(f"k9s exited{f' ({self.error})' if self.error else ''}.\n", "bold #FFFF00")
        text.append("\n")
        text.append(" Enter ", "bold #FFFFFF on #0000FF")
        text.append(f"  connect: ssh {t.label}, then run ", "#FFFFFF")
        text.append(t.command, "bold #00FF00")
        text.append(" in this panel\n\n", "#FFFFFF")
        text.append("Device key", "bold #00FF00")
        text.append(" (add to ~/.ssh/authorized_keys on the node):\n", "#FFFFFF")
        text.append(f"{self.public_key}\n\n", "#FFFFFF")
        text.append("Using Tailscale SSH on the node? No key needed.\n", "#FFFFFF")
        text.append(f"Host key: trusted on first use, pinned in {t.known_hosts_path}\n", "#FFFFFF")
        text.append("k9s gets the E-ink skin from ~/.config/tui-os/k9s on the node "
                    "(your own k9s config is untouched).", "#FFFFFF")
        return text

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        self._render_state()
        self.app.call_after_refresh(lambda: self.focus_target.focus() if self.is_mounted else None)

    # ----------------------------------------------------------- lifecycle
    def activate(self) -> None:
        """Called when the tab is shown; connects once if autoconnect is on."""
        if not self._activated:
            self._activated = True
            if self.settings.k9s_autoconnect and self.state == "idle":
                self.connect()

    @on(ConnectRequested)
    def _connect_requested(self) -> None:
        if self.state != "connecting":
            self.connect()

    @work(exclusive=True, group="k9s", exit_on_error=False)
    async def connect(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.terminal.reset()
        self._set_state("connecting")
        # Show the terminal invisibly-sized first so we know its cell grid.
        self.terminal.display = True
        self.query_one(K9sIdle).display = False
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
