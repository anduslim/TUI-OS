"""Copy screen: Ctrl+Shift+C (or the title-bar button) copies the active tab's text."""

from __future__ import annotations

import base64
from dataclasses import replace

from tui_os.host.bridge import clipboard_texts
from tui_os.host.keymap import PASTE_KEYS, NamedKey, translate_keydown
from tui_os.widgets.term_emulator import TerminalEmulator, encode_key


def test_osc52_is_read_back_for_the_host_clipboard():
    payload = base64.b64encode("héllo\nworld".encode()).decode()
    data = f"\x1b[?25l\x1b]52;c;{payload}\x07\x1b]52;c;{payload}\x1b\\"
    assert clipboard_texts(data) == ["héllo\nworld", "héllo\nworld"]
    assert clipboard_texts("\x1b]52;c;!!notbase64\x07") == []


def test_ctrl_shift_keys():
    assert translate_keydown(99, "c", ["ctrl", "shift"]) == NamedKey("ctrl+shift+c", "\x03")
    assert "ctrl+shift+v" in PASTE_KEYS
    assert encode_key("ctrl+shift+x", "\x18") == "\x18"  # still Ctrl+X for remote programs
    assert "ctrl+shift+c" in TerminalEmulator.RESERVED


async def test_copy_screen_from_a_terminal_tab(settings):
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.shell import ShellPane

    app = TuiOS(replace(settings, start_tab="shell"), telemetry_source=DemoTelemetry(seed=1))
    async with app.run_test(size=(147, 46)) as pilot:
        pane = app.query_one(ShellPane)
        term = pane.terminal
        term.display = True
        await pilot.pause()
        term.feed("olares@olares:~$ kubectl get pods\r\nNAME   READY\r\nhermes 3/3\r\n")
        await pilot.press("ctrl+shift+c")
        await pilot.pause()
        assert app._clipboard == "olares@olares:~$ kubectl get pods\nNAME   READY\nhermes 3/3"


async def test_copy_screen_from_a_widget_tab_via_the_title_bar(make_app):
    from tui_os.app import TitleBar

    app = make_app()
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("ctrl+2")  # Mail
        await pilot.pause()
        bar = app.query_one(TitleBar)
        start, _end = bar._copy_span
        await pilot.click(TitleBar, offset=(start + 2, 0))
        await pilot.pause()
        text = app._clipboard
        assert text and text == app.screen_text()[0]
        assert "│" not in text.splitlines()[0][:1]  # inside the pane, not the frame
