"""Browser tab with a native WebView host (the APK path), via a fake host."""

from __future__ import annotations

import re

from tui_os.host.eink_web import EINK_CSS
from tui_os.host.services import WebViewEvent
from tui_os.palette import TOKENS
from tui_os.widgets.webview import WebBrowserPane, WebSurface


class FakeHost:
    webview_available = True

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        if name in {"show_webview", "hide_webview", "load_url", "load_html", "go_back", "reload", "focus_webview"}:
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)

    def last(self, name):
        return next((c for c in reversed(self.calls) if c[0] == name), None)


def make(settings, host):
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry

    return TuiOS(settings, telemetry_source=DemoTelemetry(seed=1), host=host)


def test_injected_css_only_uses_palette_colours():
    colours = {c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}\b", EINK_CSS)}
    assert colours <= set(TOKENS) and colours
    assert "transition: none" in EINK_CSS and "scroll-behavior: auto" in EINK_CSS
    assert "::selection { background-color: #0000FF !important; color: #FFFFFF" in EINK_CSS


async def test_webview_replaces_text_browser_and_tracks_region(settings):
    host = FakeHost()
    app = make(settings, host)
    async with app.run_test(size=(147, 46)) as pilot:
        assert isinstance(app.query_one("#browser-pane"), WebBrowserPane)
        assert host.last("load_html")[2] == "about:home"  # home page in the WebView
        assert host.last("show_webview") is None  # hidden until its tab is shown

        await pilot.press("f4")
        await pilot.pause(0.3)
        surface = app.query_one(WebSurface)
        r = surface.content_region
        assert host.last("show_webview")[1] == (r.x, r.y, r.width, r.height)
        assert r.x == 30 + 1 + 1  # telemetry panel, frame border, focus gutter
        assert host.last("focus_webview") is not None  # F4 puts keys into the page

        await pilot.press("ctrl+b")  # telemetry hides → overlay moves and widens by 30
        await pilot.pause(0.3)
        x2, _y, w2, _h = host.last("show_webview")[1]
        assert x2 == r.x - 30 and w2 == r.width + 30

        await pilot.press("f1")  # leaving the tab hides the native view
        await pilot.pause(0.3)
        assert host.calls[-1][0] in ("hide_webview",) or host.last("hide_webview") is not None


async def test_location_bar_events_and_key_release(settings):
    host = FakeHost()
    app = make(settings, host)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f4")
        await pilot.pause(0.2)
        pane = app.query_one(WebBrowserPane)

        # Page hands keys back (Esc in the WebView) → TUI keys work again.
        app.post_message(WebViewEvent("released"))
        await pilot.pause()
        assert not pane.in_page and app.focused is app.query_one(WebSurface)

        await pilot.press("u")
        await pilot.press(*"olares-one:8080", "enter")
        await pilot.pause()
        assert host.last("load_url")[1] == "http://olares-one:8080"  # tailnet host
        assert pane.in_page

        app.post_message(WebViewEvent("finished", "http://olares-one:8080/", "Olares"))
        await pilot.pause()
        assert pane.title == "Olares" and "Olares" in str(app.query_one("#browser-status").render())

        app.post_message(WebViewEvent("navigate", "about:help"))  # about: link clicked in page
        await pilot.pause()
        assert host.last("load_html")[2] == "about:help"

        app.post_message(WebViewEvent("released"))
        await pilot.pause()
        await pilot.press("b", "r")
        assert host.last("go_back") and host.last("reload")


async def test_desktop_keeps_text_browser(settings):
    from tui_os.widgets.browser import BrowserPane

    app = make(settings, None)
    async with app.run_test(size=(147, 46)):
        assert isinstance(app.query_one("#browser-pane"), BrowserPane)
