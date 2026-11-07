"""Browser tab backed by the Android WebView (APK only).

The TUI keeps the location bar and status bar. In between sits a
:class:`WebSurface`, an empty placeholder whose cell region is sent to the
host every time it's shown, moved, resized or hidden. The native WebView is
laid exactly over it, inside the panel border, so the page is aligned to the
cell grid.

Keyboard model:

* F4 (or Enter on the surface): keys go to the page. The hardware keyboard
  drives the WebView natively: typing, Tab, arrows, PgUp/PgDn.
* Esc in the page: keys come back to the TUI. ``u`` goes to the URL bar,
  ``b`` back, ``r`` reload, Enter returns to the page.
* F1–F5, Ctrl+B, Ctrl+L and Ctrl+Q always reach the TUI, even from the page.
"""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from .. import mock_data
from ..host.services import HostServices, WebViewEvent
from ..services import web

STYLE_BAR = "bold #FFFFFF on #0000FF"


class WebSurface(Static, can_focus=True):
    """Placeholder the native WebView is laid over."""

    BINDINGS = [
        Binding("enter", "enter_page", "Page"),
        Binding("u,g", "focus_location", "Goto URL"),
        Binding("b", "back", "Back"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, host: HostServices, **kw) -> None:
        super().__init__("", **kw)
        self.host = host
        self.shown_region: tuple[int, int, int, int] | None = None

    def _report(self) -> None:
        r = self.content_region
        if not self.display or not self.is_mounted or r.width <= 0 or r.height <= 0:
            return
        region = (r.x, r.y, r.width, r.height)
        if region != self.shown_region:
            self.shown_region = region
            self.host.show_webview(region)

    def hide_native(self) -> None:
        if self.shown_region is not None:
            self.shown_region = None
            self.host.hide_webview()

    def on_show(self) -> None:
        self.call_after_refresh(self._report)

    def on_resize(self) -> None:
        self.call_after_refresh(self._report)

    def on_hide(self) -> None:
        self.hide_native()

    def on_unmount(self) -> None:
        self.hide_native()

    def action_enter_page(self) -> None:
        self.parent_pane.enter_page()

    def action_focus_location(self) -> None:
        self.parent_pane.focus_location()

    def action_back(self) -> None:
        self.host.go_back()

    def action_reload(self) -> None:
        self.host.reload()

    @property
    def parent_pane(self) -> "WebBrowserPane":
        return self.query_ancestor(WebBrowserPane)


class WebBrowserPane(Vertical):
    def __init__(self, host: HostServices, *, home: str = "about:home", id: str | None = None) -> None:
        super().__init__(id=id)
        self.host = host
        self.home = home
        self.url = ""
        self.title = ""
        self.page_loading = False
        self.in_page = False

    def compose(self) -> ComposeResult:
        location = Input(placeholder="URL, tailnet host or search terms", id="location")
        location.cursor_blink = False
        yield location
        yield WebSurface(self.host, id="websurface")
        yield Static("", id="browser-status", classes="bar")

    def on_mount(self) -> None:
        self.navigate(self.home)

    @property
    def focus_target(self) -> WebSurface:
        return self.query_one(WebSurface)

    # ---------------------------------------------------------- navigation
    def navigate(self, raw: str) -> None:
        url = web.normalise_url(raw)
        self.url, self.page_loading = url, True
        if url.startswith("about:"):
            html = mock_data.ABOUT_PAGES.get(url) or (
                f"<title>Not found</title><h1>{url}</h1><p>No such page.</p>"
            )
            self.host.load_html(html, url)
        else:
            self.host.load_url(url)
        self.query_one("#location", Input).value = url
        self._status()

    def enter_page(self) -> None:
        surface = self.query_one(WebSurface)
        surface.call_after_refresh(surface._report)
        self.in_page = True
        self.query_one(WebSurface).focus()
        self.host.focus_webview()
        self._status()

    def focus_location(self) -> None:
        self.in_page = False
        loc = self.query_one("#location", Input)
        loc.focus()
        loc.value = ""
        self._status()

    def _status(self) -> None:
        state = "loading…" if self.page_loading else (self.title or self.url)
        keys = "Esc: keys to TUI" if self.in_page else "Enter: into page · u: URL · b: back · r: reload"
        self.query_one("#browser-status", Static).update(
            Text(f" {state}  ·  {self.url}  ·  {keys}".ljust(400), STYLE_BAR)
        )

    # --------------------------------------------------------------- events
    @on(Input.Submitted, "#location")
    def _go(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.navigate(event.value)
        self.enter_page()

    def on_web_view_event(self, event: WebViewEvent) -> None:
        if event.kind == "started":
            self.page_loading, self.url = True, event.url or self.url
        elif event.kind == "finished":
            self.page_loading = False
            self.url = event.url or self.url
            self.title = event.title
        elif event.kind == "error":
            self.page_loading = False
            self.title = f"error: {event.title}"
            self.app.notify(f"{event.url}: {event.title}", severity="error")
        elif event.kind == "navigate":
            self.navigate(event.url)
        elif event.kind == "released":
            self.in_page = False
            self.query_one(WebSurface).focus()
        if event.kind in ("started", "finished") and not self.query_one("#location", Input).has_focus:
            self.query_one("#location", Input).value = self.url
        self._status()

    def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+l":
            self.focus_location()
            event.stop()
