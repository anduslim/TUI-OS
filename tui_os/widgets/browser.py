"""Browser tab: w3m-style text viewport.

``about:`` pages are served from mock data; http(s) URLs are fetched for real
with httpx and rendered to text with numbered links. Navigation is
page-based (Space / PgUp / PgDn) — no smooth scrolling.
"""

from __future__ import annotations

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from ..services import web

STYLE_BAR = "bold #FFFFFF on #0000FF"


class BrowserViewport(VerticalScroll):
    can_focus = True

    BINDINGS = [
        Binding("space,pagedown", "page(1)", "Page ▼", show=False),
        Binding("pageup,shift+space", "page(-1)", "Page ▲", show=False),
        Binding("home", "scroll_home_now", show=False),
        Binding("end", "scroll_end_now", show=False),
    ]

    def action_page(self, direction: int) -> None:
        if direction > 0:
            self.scroll_page_down(animate=False)
        else:
            self.scroll_page_up(animate=False)

    def action_scroll_home_now(self) -> None:
        self.scroll_home(animate=False)

    def action_scroll_end_now(self) -> None:
        self.scroll_end(animate=False)


class BrowserPane(Vertical):
    BINDINGS = [
        Binding("u,g", "focus_location", "Goto URL"),
        Binding("b", "back", "Back"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, *, home: str = "about:home", id: str | None = None) -> None:
        super().__init__(id=id)
        self.home = home
        self.history: list[str] = []
        self.page: web.Page | None = None
        self._client: httpx.AsyncClient | None = None

    def compose(self) -> ComposeResult:
        location = Input(placeholder="URL, search terms, or link number", id="location")
        location.cursor_blink = False
        yield location
        with BrowserViewport(id="viewport"):
            yield Static("", id="page-body")
        yield Static("", id="browser-status", classes="bar")

    def on_mount(self) -> None:
        self._client = web.make_client()
        self.navigate(self.home)

    async def on_unmount(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    # ---------------------------------------------------------------- loading
    def _status(self, message: str) -> None:
        self.query_one("#browser-status", Static).update(Text(f" {message}".ljust(300), STYLE_BAR))

    def navigate(self, url: str, *, push: bool = True) -> None:
        url = web.normalise_url(url)
        if push and self.page is not None and self.page.url != url:
            self.history.append(self.page.url)
        self._load(url)

    @work(exclusive=True, group="browser", exit_on_error=False)
    async def _load(self, url: str) -> None:
        self._status(f"Fetching {url} …")
        assert self._client is not None
        try:
            page = await web.fetch_page(url, self._client)
        except httpx.HTTPStatusError as exc:
            page = web.render_html(
                f"<title>HTTP {exc.response.status_code}</title><h1>HTTP {exc.response.status_code}</h1>"
                f"<p>{url}</p><p><a href='about:home'>Start page</a></p>",
                url,
            )
        except httpx.HTTPError as exc:
            page = web.render_html(
                f"<title>Error</title><h1>Can't load page</h1><p>{url}</p>"
                f"<p>{type(exc).__name__}: {exc}</p><p><a href='about:home'>Start page</a></p>",
                url,
            )
        self.page = page
        self.query_one("#page-body", Static).update(page.body)
        self.query_one("#viewport", BrowserViewport).scroll_home(animate=False)
        location = self.query_one("#location", Input)
        location.value = page.url
        self._status(f"{page.title}  ·  {len(page.links)} links  ·  {page.url}")

    # ----------------------------------------------------------------- events
    @on(Input.Submitted, "#location")
    def _go(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value.isdigit() and self.page is not None:
            n = int(value)
            if 1 <= n <= len(self.page.links):
                self.navigate(self.page.links[n - 1])
            else:
                self.app.notify(f"No link [{n}]", severity="warning")
                return
        else:
            self.navigate(value)
        self.query_one("#viewport", BrowserViewport).focus()

    # ----------------------------------------------------------------- actions
    def action_focus_location(self) -> None:
        location = self.query_one("#location", Input)
        location.focus()
        location.value = ""

    def action_back(self) -> None:
        if self.history:
            self.navigate(self.history.pop(), push=False)
        else:
            self.app.notify("No history", severity="warning")

    def action_reload(self) -> None:
        if self.page is not None:
            self.navigate(self.page.url, push=False)
