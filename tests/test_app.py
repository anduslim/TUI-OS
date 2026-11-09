from __future__ import annotations

import asyncio

from textual.widgets import TabbedContent

from tui_os.widgets.telemetry_panel import MetricRow, TelemetryPanel
from tui_os.widgets.terminal import ResponseBlock, TerminalPane


async def test_layout_and_telemetry_toggle(make_app):
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        panel = app.query_one(TelemetryPanel)
        workspace = app.query_one("#workspace")
        assert panel.display and panel.outer_size.width == 30
        narrow = workspace.outer_size.width
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert not panel.display
        assert workspace.outer_size.width == 160 > narrow
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert panel.display and workspace.outer_size.width == narrow


async def test_telemetry_worker_updates_panel(make_app):
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query_one("#m-cpu-temp", MetricRow)._last[0] != "--":
                break
        assert "°C" in app.query_one("#m-cpu-temp", MetricRow)._last[0]
        assert "GB" in app.query_one("#m-vram", MetricRow)._last[0]


async def test_terminal_streams_reply_without_blocking(make_app):
    app = make_app(tokens=["Hello", ", ", "`world`", "!"])
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        await pilot.click("#prompt")
        await pilot.press(*"hi there", "enter")
        pane = app.query_one(TerminalPane)
        for _ in range(40):
            await pilot.pause(0.05)
            if not pane.is_streaming and pane.query(ResponseBlock):
                break
        block = pane.query_one(ResponseBlock)
        assert block.text == "Hello, `world`!"
        assert pane.context == [1, 2, 3]
        assert "Hello, world!" in str(block.render())


async def test_tab_switching_shortcuts(make_app):
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        tabs = app.query_one("#workspace", TabbedContent)
        for key, tab in (("f2", "mail"), ("f3", "calendar"), ("f4", "browser"), ("f5", "k9s"),
                         ("f1", "shell"), ("f6", "terminal")):
            await pilot.press(key)
            await pilot.pause()
            assert tabs.active == tab


async def test_ctrl_digit_switches_tabs_even_from_the_shell(make_app):
    """Boox: F-keys and Alt+digit belong to the system, so Ctrl+1..6 switch tabs,
    including while the SSH terminal (which takes every other key) has focus."""
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        tabs = app.query_one("#workspace", TabbedContent)
        for key, tab in (("ctrl+2", "mail"), ("ctrl+3", "calendar"), ("ctrl+4", "browser"),
                         ("ctrl+5", "k9s"), ("ctrl+6", "terminal"), ("ctrl+1", "shell")):
            await pilot.press(key)
            await pilot.pause()
            assert tabs.active == tab
        from tui_os.widgets.term_emulator import TerminalEmulator
        term = app.query_one("#shell-pane").query_one(TerminalEmulator)
        term.display = True
        term.focus()
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()
        assert tabs.active == "mail"


async def test_browser_follows_numbered_link(make_app):
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.press("f4")
        await pilot.pause(0.2)
        browser = app.query_one("#browser-pane")
        assert browser.page.url == "about:home"
        await pilot.press("u", "1", "enter")
        await pilot.pause(0.2)
        assert browser.page.url == "about:help"
        await pilot.press("b")
        await pilot.pause(0.2)
        assert browser.page.url == "about:home"


async def test_escape_cancels_stream(settings):
    import json

    import httpx

    from tui_os.app import TuiOS
    from tui_os.services.ollama import OllamaClient
    from tui_os.services.telemetry import DemoTelemetry

    async def slow_body():
        for i in range(1000):
            yield (json.dumps({"response": f"t{i} ", "done": False}) + "\n").encode()
            await asyncio.sleep(0.01)

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m"}]})
        return httpx.Response(200, content=slow_body())

    client = OllamaClient("http://o:11434", "m", transport=httpx.MockTransport(handler))
    app = TuiOS(settings, telemetry_source=DemoTelemetry(seed=1), ollama_client=client)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.click("#prompt")
        await pilot.press("x", "enter")
        pane = app.query_one(TerminalPane)
        await pilot.pause(0.3)
        assert pane.is_streaming
        # UI stays responsive mid-stream: toggling the sidebar works.
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert not app.query_one(TelemetryPanel).display
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not pane.is_streaming
        partial = pane.query_one(ResponseBlock).text
        assert partial.startswith("t0 ") and "t999" not in partial
        assert any("cancelled" in str(e.render()) for e in pane.query("ChatEntry"))


async def test_tab_switch_from_mail_does_not_bounce_back(make_app):
    """Regression: hiding the mail pane used to refocus #mail-pager, which
    made TabbedContent jump back to Mail."""
    app = make_app()
    async with app.run_test(size=(150, 42)) as pilot:
        tabs = app.query_one("#workspace", TabbedContent)
        await pilot.press("f2")
        await pilot.pause(0.3)
        assert app.focused.id == "mail-index"
        await pilot.press("f3")
        await pilot.pause(0.3)
        assert tabs.active == "calendar"
        assert app.focused.id == "calendar-pane"


async def test_mail_subject_not_parsed_as_markup(make_app):
    app = make_app()
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        table = app.query_one("#mail-index")
        subjects = [str(table.get_row_at(i)[4]) for i in range(table.row_count)]
        assert any(s.startswith("[tui-os]") for s in subjects)


def test_version_comes_from_the_ci_build_file(monkeypatch, tmp_path):
    import sys
    import types

    from tui_os.version import app_version

    monkeypatch.delitem(sys.modules, "tui_os._build_version", raising=False)
    assert app_version() == "dev"  # desktop checkout: no build file
    fake = types.ModuleType("tui_os._build_version")
    fake.VERSION = "1.1.6"
    monkeypatch.setitem(sys.modules, "tui_os._build_version", fake)
    assert app_version() == "1.1.6"


async def test_title_bar_shows_the_version(make_app, monkeypatch):
    import sys
    import types

    fake = types.ModuleType("tui_os._build_version")
    fake.VERSION = "9.8.7"
    monkeypatch.setitem(sys.modules, "tui_os._build_version", fake)
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        assert "TUI-OS 9.8.7" in str(app.query_one("#titlebar").render())
