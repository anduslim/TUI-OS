"""TUI-OS application shell.

Layout (landscape)::

    ┌ title bar ─────────────────────────────────────────────────────────┐
    │ ┌ OLARES ONE ─┐ ┌ Shell │ Mail │ Calendar │ Browser │ K9s │ LLM ─┐ │
    │ │ telemetry   │ │                                                │ │
    │ │ (Ctrl+B)    │ │   workspace (1fr — claims full width when the  │ │
    │ │ 30 cols     │ │   telemetry panel is hidden)                   │ │
    │ └─────────────┘ └────────────────────────────────────────────────┘ │
    └ footer (key bindings) ─────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.driver import Driver
from textual.message import Message
from textual.widgets import Footer, Static, TabbedContent, TabPane

from .config import Settings
from .services.ollama import OllamaClient
from .services.llm import make_llm_client
from .services.openai_chat import OpenAIChatClient
from .services.telemetry import (
    DemoTelemetry,
    PrometheusTelemetry,
    TelemetrySnapshot,
    TelemetrySource,
)
from .theme import EINK_THEME
from .widgets.browser import BrowserPane
from .widgets.calendar import CalendarPane
from .widgets.k9s import K9sPane, SessionFactory
from .widgets.shell import ShellPane
from .widgets.webview import WebBrowserPane
from .host.services import HostServices, WebViewEvent
from .widgets.mail import MailPane
from .widgets.telemetry_panel import TelemetryPanel
from .widgets.terminal import TerminalPane
from .widgets.unlock import UnlockScreen
from .services.vault import VaultClient, VaultConfig

TABS = ("shell", "mail", "calendar", "browser", "k9s", "terminal")


class TelemetryUpdated(Message):
    """Posted by the telemetry worker each poll."""

    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot


class TitleBar(Static):
    """One-line header. Clock has minute resolution (one repaint a minute)."""

    def __init__(self, host: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.host = host
        self._last = ""

    def on_mount(self) -> None:
        self.tick()
        # Align ticks to the minute boundary so the clock never lags.
        self.set_timer(60 - time.localtime().tm_sec + 0.05, self._start_minute_timer)

    def _start_minute_timer(self) -> None:
        self.tick()
        self.set_interval(60, self.tick)

    def tick(self) -> None:
        stamp = time.strftime("%a %d %b  %H:%M")
        if stamp == self._last:
            return
        self._last = stamp
        text = Text()
        text.append(" TUI-OS ", "bold #000000 on #00FF00")
        text.append(f"  olares://{self.host}", "bold #FFFFFF")
        right = f"{stamp} "
        text.append(" " * max(1, self.size.width - text.cell_len - len(right)))
        text.append(right, "bold #FFFFFF")
        self.update(text)

    def on_resize(self) -> None:
        self._last = ""
        self.tick()


class TuiOS(App[None]):
    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "TUI-OS"
    # The command palette is a modal overlay with its own (grey) styling and
    # a footer entry; nothing in the E-ink spec needs it.
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+b", "toggle_telemetry", "Telemetry", priority=True),
        Binding("ctrl+1,f1,alt+1", "show_tab('shell')", "Shell", priority=True),
        Binding("ctrl+2,f2,alt+2", "show_tab('mail')", "Mail", priority=True),
        Binding("ctrl+3,f3,alt+3", "show_tab('calendar')", "Cal", priority=True),
        Binding("ctrl+4,f4,alt+4", "show_tab('browser')", "Web", priority=True),
        Binding("ctrl+5,f5,alt+5", "show_tab('k9s')", "K9s", priority=True),
        Binding("ctrl+6,f6,alt+6", "show_tab('terminal')", "LLM", priority=True),
        Binding("ctrl+r", "refresh_screen", "Redraw", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        telemetry_source: TelemetrySource | None = None,
        ollama_client: "OllamaClient | OpenAIChatClient | None" = None,
        driver_class: type[Driver] | None = None,
        k9s_session_factory: SessionFactory | None = None,
        host: HostServices | None = None,
        vault_client: VaultClient | None = None,
    ) -> None:
        # driver_class lets a non-terminal host (the Android/Kivy shell)
        # supply its own Driver instead of the TTY-based Linux driver.
        super().__init__(driver_class=driver_class)
        self.settings = settings or Settings.load()
        self._k9s_factory = k9s_session_factory
        self._vault_client = vault_client
        #: Native services when embedded in the APK (WebView); None in a terminal.
        self.host = host
        # E-ink: every animation (tab underline slide, smooth scroll, fades)
        # completes instantly. One state change -> one repaint.
        self.animation_level = "none"
        self.telemetry_source: TelemetrySource = telemetry_source or self._make_telemetry()
        self.ollama = ollama_client or make_llm_client(self.settings)
        # Fixed full-scale reference for network bars (adapts upward to peaks).
        self._net_peak = 12.5e6  # 100 Mbit/s

    @property
    def _start_tab(self) -> str:
        tab = self.settings.start_tab
        return tab if tab in TABS else "shell"

    def _make_telemetry(self) -> TelemetrySource:
        if self.settings.demo_mode:
            return DemoTelemetry()
        return PrometheusTelemetry(self.settings.resolved_prometheus_url, self.settings.queries)

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        source_label = (
            "demo data" if self.settings.demo_mode else self.settings.resolved_prometheus_url
        )
        yield TitleBar(self.settings.olares_host, id="titlebar")
        with Horizontal(id="desktop"):
            yield TelemetryPanel(source_label=source_label, id="telemetry")
            with TabbedContent(initial=self._start_tab, id="workspace"):
                with TabPane("1 Shell", id="shell"):
                    yield ShellPane(self.settings, session_factory=self._k9s_factory, id="shell-pane")
                with TabPane("2 Mail", id="mail"):
                    yield MailPane(self.settings, id="mail-pane")
                with TabPane("3 Calendar", id="calendar"):
                    yield CalendarPane(self.settings, id="calendar-pane")
                with TabPane("4 Browser", id="browser"):
                    if self.host is not None and self.host.webview_available:
                        yield WebBrowserPane(self.host, home=self.settings.browser_home, id="browser-pane")
                    else:
                        yield BrowserPane(home=self.settings.browser_home, id="browser-pane")
                with TabPane("5 K9s (Cluster)", id="k9s"):
                    yield K9sPane(self.settings, session_factory=self._k9s_factory, id="k9s-pane")
                with TabPane("6 LLM Terminal", id="terminal"):
                    yield TerminalPane(
                        self.ollama,
                        flush_interval=self.settings.stream_flush_interval,
                        system_prompt=self.settings.ollama_system_prompt,
                        id="terminal-pane",
                    )
        yield Footer(compact=True)

    def on_mount(self) -> None:
        self.register_theme(EINK_THEME)
        self.theme = EINK_THEME.name
        self.query_one("#telemetry").display = self.settings.telemetry_visible
        self.telemetry_loop()
        self._focus_tab(self._start_tab)
        self._activate(self._start_tab)
        self._maybe_unlock_vault()

    # ------------------------------------------------------------------- vault
    def _maybe_unlock_vault(self) -> None:
        """Ask for the Vaultwarden master password; config.toml values stay as fallback."""
        client = self._vault_client
        if client is None and self.settings.vault_configured:
            s = self.settings
            client = VaultClient(VaultConfig(
                url=s.vault_url, email=s.vault_email, item=s.vault_item,
                client_id=s.vault_client_id, client_secret=s.vault_client_secret,
                device_id_path=s.data_dir / "vault_device_id",
            ))
        if client is None:
            return

        def unlocked(values: dict[str, str] | None) -> None:
            if not values:
                return
            self.settings = self.settings.with_secrets(values)
            self.query_one(MailPane).reconfigure(self.settings)
            self.query_one(CalendarPane).reconfigure(self.settings)
            self.notify(f"Vault unlocked: {', '.join(sorted(values))}", title="Vault")

        self.push_screen(UnlockScreen(client), unlocked)

    async def on_unmount(self) -> None:
        await asyncio.gather(
            self.telemetry_source.aclose(), self.ollama.aclose(), return_exceptions=True
        )

    # --------------------------------------------------------------- telemetry
    @work(exclusive=True, group="telemetry", exit_on_error=False)
    async def telemetry_loop(self) -> None:
        """Background poller: fetch → post message → sleep, until cancelled.

        Runs as an async worker on the app's event loop; network I/O is
        awaited, so the UI never blocks. A failing poll is reported in the
        panel and retried on the next tick rather than killing the loop.
        """
        interval = max(1.0, self.settings.telemetry_interval)
        while True:
            started = time.monotonic()
            try:
                snapshot = await self.telemetry_source.fetch()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                snapshot = TelemetrySnapshot(source="offline", error=str(exc) or type(exc).__name__)
            self.post_message(TelemetryUpdated(snapshot))
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))

    def on_telemetry_updated(self, message: TelemetryUpdated) -> None:
        snap = message.snapshot
        for value in (snap.net_rx, snap.net_tx):
            if value and value > self._net_peak:
                self._net_peak = value
        panel = self._panel()
        if panel is None:  # message arrived during shutdown
            return
        # Hidden widgets are not painted, but keep their state current so the
        # panel is correct the instant it is re-opened.
        panel.apply_snapshot(snap, max_net=self._net_peak)

    def on_web_view_event(self, event: WebViewEvent) -> None:
        """Host WebView events arrive at the app; hand them to the browser pane."""
        pane = self.query(WebBrowserPane).first() if self.query(WebBrowserPane) else None
        if pane is not None:
            pane.on_web_view_event(event)

    def on_terminal_pane_llm_state(self, message: TerminalPane.LlmState) -> None:
        panel = self._panel()
        if panel is not None:
            panel.set_llm(message.model, message.state)

    def _panel(self) -> TelemetryPanel | None:
        """The telemetry panel, or None once the screen is being torn down."""
        panels = self.query(TelemetryPanel)
        return panels.first() if panels else None

    # ----------------------------------------------------------------- actions
    def action_toggle_telemetry(self) -> None:
        panel = self.query_one(TelemetryPanel)
        # display:none removes it from layout; the 1fr workspace reflows to
        # full width in the same frame (no slide animation -> no ghosting).
        panel.display = not panel.display

    def action_show_tab(self, tab: str) -> None:
        # Drop focus first. Otherwise, when the old pane hides, Textual
        # passes focus to the next widget *in that same pane*, and
        # TabbedContent switches back to the pane that holds focus.
        self.screen.set_focus(None)
        self.query_one("#workspace", TabbedContent).active = tab
        self._focus_tab(tab)

    _FOCUS = {"terminal": "#prompt", "mail": "#mail-index", "calendar": "#calendar-pane"}

    def _focus_target(self, tab: str):
        """Panes with changing internals (shell, browser, k9s) say what to focus."""
        if tab in self._FOCUS:
            return self.query_one(self._FOCUS[tab])
        return self.query_one(f"#{tab}-pane").focus_target

    def _activate(self, tab: str) -> None:
        """SSH tabs connect the first time they are shown."""
        pane = self.query(f"#{tab}-pane")
        if pane and isinstance(pane.first(), (ShellPane, K9sPane)):
            pane.first().activate()

    def _focus_tab(self, tab: str) -> None:
        def apply() -> None:
            # Stale request (user already moved on): do nothing, otherwise
            # TabbedContent would switch back to the pane we focus.
            if self.query_one("#workspace", TabbedContent).active == tab:
                self._focus_target(tab).focus()

        self.call_after_refresh(apply)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id in TABS:
            self._focus_tab(event.pane.id)
            self._activate(event.pane.id)
            if event.pane.id == "browser" and self.query(WebBrowserPane):
                self.call_after_refresh(self.query_one(WebBrowserPane).enter_page)

    def action_refresh_screen(self) -> None:
        """Full repaint — use to clear accumulated E-ink ghosting."""
        self.refresh(layout=True)
        self.screen.refresh(repaint=True)
