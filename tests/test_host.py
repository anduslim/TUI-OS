"""Android host layer: keymap (pure) and the embedded-terminal bridge.

The bridge tests run the real TuiOS app through EmbeddedDriver + pyte,
which is exactly the code path the APK uses, minus Kivy's painting.
"""

from __future__ import annotations

import time

import pytest

from tui_os.config import Settings
from tui_os.host.bridge import TerminalBridge, _colour
from tui_os.host.keymap import CharKey, NamedKey, translate_keydown


@pytest.mark.parametrize(
    ("keycode", "codepoint", "mods", "expected"),
    [
        (98, "b", ["ctrl"], NamedKey("ctrl+b", "\x02")),
        (113, "q", ["ctrl"], NamedKey("ctrl+q", "\x11")),
        (283, None, [], NamedKey("f2")),
        (50, "2", ["alt"], NamedKey("alt+2")),
        (27, None, [], NamedKey("escape", "\x1b")),
        (13, None, [], NamedKey("enter", "\r")),
        (9, None, ["shift"], NamedKey("shift+tab")),
        (281, None, [], NamedKey("pagedown")),
        (273, None, ["ctrl"], NamedKey("ctrl+up")),
        (97, "a", [], CharKey("a")),
        (97, "a", ["shift"], CharKey("A")),
        (49, "1", ["shift"], CharKey("!")),
        (32, " ", [], CharKey(" ")),
        (1073742049, None, ["shift"], None),  # bare shift key
    ],
)
def test_translate_keydown(keycode, codepoint, mods, expected):
    assert translate_keydown(keycode, codepoint, mods) == expected


def test_colour_mapping():
    assert _colour("00ff00", "FFFFFF") == "00FF00"
    assert _colour("default", "000000") == "000000"
    assert _colour("brightblue", "FFFFFF") == "0000FF"


def _wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def bridge():
    from tui_os.app import TuiOS

    b = TerminalBridge(
        lambda dc: TuiOS(Settings(demo_mode=True, telemetry_interval=1.0), driver_class=dc),
        cols=147,
        rows=46,
    )
    b.start()
    assert b.ready.wait(10), b.error
    yield b
    b.stop()
    assert b.finished.wait(5)
    assert b.error is None, b.error


def screen(b):
    return "\n".join(b.text_lines())


def test_bridge_renders_textual_app(bridge):
    assert _wait_for(lambda: "OLARES ONE" in screen(bridge) and "°C" in screen(bridge))
    text = screen(bridge)
    assert "1 Terminal" in text and "4 Browser" in text


def test_bridge_keys_drive_the_app(bridge):
    assert _wait_for(lambda: "1 Terminal" in screen(bridge))
    bridge.send_key("f2")
    assert _wait_for(lambda: "NeoMutt" in screen(bridge))
    bridge.send_key("ctrl+b")  # hide telemetry: workspace starts at column 0
    assert _wait_for(lambda: "OLARES ONE" not in screen(bridge))
    bridge.send_key("f1")
    assert _wait_for(lambda: bridge.app.focused is not None and bridge.app.focused.id == "prompt")
    bridge.send_text("/help")
    bridge.send_key("enter", "\r")
    assert _wait_for(lambda: "/models" in screen(bridge))


def test_bridge_dirty_rows_carry_design_colours(bridge):
    assert _wait_for(lambda: "OLARES ONE" in screen(bridge))
    time.sleep(0.3)
    bridge.mark_all_dirty()
    rows = dict(bridge.take_dirty_rows())
    assert len(rows) == 46
    colours = {(r.fg, r.bg) for runs in rows.values() for r in runs}
    allowed = {"000000", "FFFFFF", "00FF00", "0000FF", "FFFF00", "FF0000"}
    used = {c for pair in colours for c in pair}
    assert used <= allowed, used - allowed
    title = rows[0][0]
    assert title.text.strip() == "TUI-OS" and title.bg == "00FF00" and title.fg == "000000"


def test_bridge_resize_and_tap(bridge):
    assert _wait_for(lambda: "2 Mail" in screen(bridge))
    bridge.resize(120, 40)
    assert _wait_for(lambda: len(bridge.text_lines()) == 40 and len(bridge.text_lines()[0]) == 120)
    assert _wait_for(lambda: tuple(bridge.app.size) == (120, 40))
    time.sleep(0.3)  # let the relayout land before hit-testing
    row = next(i for i, line in enumerate(bridge.text_lines()) if "2 Mail" in line)
    col = bridge.text_lines()[row].index("2 Mail") + 2
    bridge.tap(col, row)
    assert _wait_for(lambda: "NeoMutt" in screen(bridge))


def test_bridge_exit_sets_finished():
    from tui_os.app import TuiOS

    exited = []
    b = TerminalBridge(
        lambda dc: TuiOS(Settings(demo_mode=True), driver_class=dc), on_exit=exited.append
    )
    b.start()
    assert b.ready.wait(10)
    b.send_key("ctrl+q", "\x11")
    assert b.finished.wait(5) and exited == [b] and b.error is None
