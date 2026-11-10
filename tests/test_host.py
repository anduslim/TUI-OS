"""Android host layer: keymap (pure) and the embedded-terminal bridge.

The bridge tests run the real TuiOS app through EmbeddedDriver + pyte,
which is exactly the code path the APK uses, minus Kivy's painting.
"""

from __future__ import annotations

import time

import pytest

from tui_os.config import Settings
from tui_os.host.bridge import TerminalBridge
from tui_os.host.keymap import CharKey, NamedKey, translate_keydown


@pytest.mark.parametrize(
    ("keycode", "codepoint", "mods", "expected"),
    [
        (98, "b", ["ctrl"], NamedKey("ctrl+b", "\x02")),
        (113, "q", ["ctrl"], NamedKey("ctrl+q", "\x11")),
        (283, None, [], NamedKey("f2")),
        (50, "2", ["alt"], NamedKey("alt+2")),
        (49, "1", ["ctrl"], NamedKey("ctrl+1")),
        (54, None, ["ctrl"], NamedKey("ctrl+6")),
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
    from rich.style import Style

    from tui_os.host.cells import style_key

    assert style_key(Style(color="#00ff00")) == ("00FF00", "000000", False, False, False)
    assert style_key(None) == ("FFFFFF", "000000", False, False, False)
    assert style_key(Style(color="bright_blue", bgcolor="yellow", bold=True))[:3] == ("0000FF", "FFFF00", True)
    assert style_key(Style(color="#ff0000", bgcolor="#ffffff", reverse=True))[:2] == ("FFFFFF", "FF0000")


def test_cell_grid_writes_segments_and_wide_chars():
    from rich.segment import Segment
    from rich.style import Style

    from tui_os.host.cells import CellGrid

    g = CellGrid(10, 2)
    g.dirty.clear()
    end = g.put_segments(0, 1, [Segment("ab", Style(color="#00ff00")), Segment("日x")])
    assert end == 6 and g.dirty == {0}
    assert g.text_lines()[0] == " ab日x    "  # 日 fills two cells; its right half is ""
    runs = g.row_runs(0)
    assert [(r.col, r.text, r.fg) for r in runs] == [(0, " ", "FFFFFF"), (1, "ab", "00FF00"), (3, "日x    ", "FFFFFF")]
    g.put_segments(0, 4, [Segment("z")])  # overwrites the right half of 日
    assert g.text_lines()[0] == " ab zx    "
    g.put_segments(1, 8, [Segment("toolong")])  # clipped at the right edge
    assert g.text_lines()[1] == "        to"


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
        lambda dc: TuiOS(Settings(demo_mode=True, telemetry_interval=1.0, start_tab="terminal",
                                  shell_autoconnect=False), driver_class=dc),
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
    assert "1 Shell" in text and "4 Browser" in text and "6 LLM Terminal" in text


def test_bridge_keys_drive_the_app(bridge):
    assert _wait_for(lambda: "6 LLM Terminal" in screen(bridge))
    bridge.send_key("f2")
    assert _wait_for(lambda: "NeoMutt" in screen(bridge))
    bridge.send_key("ctrl+b")  # hide telemetry: workspace starts at column 0
    assert _wait_for(lambda: "OLARES ONE" not in screen(bridge))
    bridge.send_key("f6")
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
    assert title.text.strip().startswith("TUI-OS ") and title.bg == "00FF00" and title.fg == "000000"


def test_bridge_resize_and_tap(bridge):
    assert _wait_for(lambda: "2 Mail" in screen(bridge))
    bridge.resize(120, 40)
    assert _wait_for(lambda: len(bridge.text_lines()) == 40 and len(bridge.text_lines()[0]) == 120)
    assert _wait_for(lambda: tuple(bridge.app.size) == (120, 40))
    # Wait for the re-render at the new size to reach the screen (the pyte
    # buffer can be blank until it does; a fixed sleep flaked on slow CI).
    assert _wait_for(lambda: "2 Mail" in screen(bridge) and "4 Browser" in screen(bridge))
    time.sleep(0.3)  # let the compositor map settle before hit-testing
    row = next(i for i, line in enumerate(bridge.text_lines()) if "2 Mail" in line)
    col = bridge.text_lines()[row].index("2 Mail") + 2
    bridge.tap(col, row)
    assert _wait_for(lambda: "NeoMutt" in screen(bridge))


def test_bridge_exit_sets_finished():
    from tui_os.app import TuiOS

    exited = []
    b = TerminalBridge(
        lambda dc: TuiOS(Settings(demo_mode=True, shell_autoconnect=False), driver_class=dc),
        on_exit=exited.append
    )
    b.start()
    assert b.ready.wait(10)
    b.send_key("ctrl+q", "\x11")
    assert b.finished.wait(5) and exited == [b] and b.error is None


# ------------------------------------------------------------ grid & glyphs
def test_grid_fits_boox_panel_at_spec_metrics():
    from tui_os.host.grid import GRID_COLS, GRID_ROWS, GridGeometry

    g = GridGeometry.fit(2480, 1860)
    assert (GRID_COLS, GRID_ROWS) == (147, 46)
    assert g.scale == pytest.approx(2480 / 1234.8)
    assert g.cell_w == pytest.approx(16.87, abs=0.01) and g.cell_h == pytest.approx(40.17, abs=0.01)
    assert g.font_px == pytest.approx(28.12, abs=0.01)
    assert g.col_edge(0) == 0 and g.col_edge(147) == 2480
    assert 0 <= g.row_edge(0) <= 10 and g.row_edge(46) <= 1860
    # Adjacent cells share edges: widths differ by at most one pixel.
    widths = {g.col_edge(c + 1) - g.col_edge(c) for c in range(147)}
    assert widths <= {16, 17}
    assert g.cell_at(2479, 1859) == (146, 45) and g.cell_at(0, 0) == (0, 0)


def test_logical_viewport_is_scale_one():
    from tui_os.host.grid import GridGeometry

    g = GridGeometry.fit(1240, 930)
    assert g.scale == pytest.approx(1240 / 1234.8) and g.font_px == pytest.approx(14.06, abs=0.01)


def test_box_glyphs_reach_cell_edges_and_join():
    from tui_os.host.glyphs import DRAWN, cell_rects

    x0, y0, x1, y1 = 100, 200, 117, 240
    def extent(rects):
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects))

    v = cell_rects("│", x0, y0, x1, y1, 2.0)
    ex = extent(v)
    assert ex[1] == y0 and ex[3] == y1 and ex[2] - ex[0] == 2  # full height, 2 px light
    # The two arms meet with no gap.
    up, down = sorted(v, key=lambda r: r[1])
    assert up[1] + up[3] >= down[1]
    assert {r[2] for r in cell_rects("┃", x0, y0, x1, y1, 2.0)} == {4}  # heavy = 2 logical px
    corner = cell_rects("┌", x0, y0, x1, y1, 2.0)
    assert any(r[0] + r[2] == x1 for r in corner) and any(r[1] + r[3] == y1 for r in corner)
    assert cell_rects("█", x0, y0, x1, y1, 2.0) == [(x0, y0, 17, 40)]
    assert cell_rects("▌", x0, y0, x1, y1, 2.0)[0][2] in (8, 9)
    assert cell_rects("A", x0, y0, x1, y1, 2.0) == []
    assert {"─", "│", "┏", "━", "╸", "█", "▁", "▊"} <= DRAWN and "·" not in DRAWN


def test_bridge_reports_errors_textual_swallows():
    """Textual catches exceptions from compose/mount, prints them to stderr
    (invisible in an APK) and returns from run(); the bridge must still
    surface the traceback so the host can show its crash screen."""
    from textual.app import App

    class Broken(App):
        def compose(self):
            raise ValueError("boom in compose")
            yield  # pragma: no cover

    exited = []
    b = TerminalBridge(lambda dc: Broken(driver_class=dc), on_exit=exited.append)
    b.start()
    assert b.finished.wait(10) and exited == [b]
    assert b.error is not None
    assert "ValueError: boom in compose" in b.error


def _type(events, delay_passes: bool = True) -> str:
    """Replay host input events through CharFallback; return what gets typed."""
    from tui_os.host.keymap import CharFallback, CharKey

    fb, typed = CharFallback(), []
    for kind, value in events:
        if kind == "key":
            fb.cancel()
            fb.key_down(CharKey(value))
        elif kind == "text":
            fb.cancel()
            fb.text_input()
            typed.append(value)
        elif kind == "tick" and delay_passes:  # fallback delay elapsed
            ch = fb.flush()
            if ch:
                typed.append(ch)
    return "".join(typed)


def test_sdl_android_key_sequence_types_each_character_once():
    """SDL 2.30 on Android: simulated key-down, text input, then the real
    key-down for every character. This used to type 'aabbcc'."""
    seq = []
    for ch in "abc":
        seq += [("key", ch), ("text", ch), ("key", ch), ("tick", None)]
    assert _type(seq) == "abc"


def test_fallback_without_any_text_input_events():
    """No IME (text events never arrive): the key itself is typed."""
    assert _type([("key", "a"), ("tick", None), ("key", "b"), ("tick", None)]) == "ab"


def test_key_then_text_on_desktop_types_once():
    assert _type([("key", "a"), ("text", "a"), ("tick", None),
                  ("key", "é"), ("text", "é"), ("tick", None)]) == "aé"


def test_paste_keys_are_recognised():
    from tui_os.host.keymap import PASTE_KEYS

    assert translate_keydown(ord("v"), "v", ["ctrl"]).key in PASTE_KEYS
    assert NamedKey("shift+insert").key in PASTE_KEYS


def test_bridge_paste_fills_the_vault_password_field():
    """A pasted master password lands whole in the masked unlock field."""
    from textual.widgets import Input

    from tui_os.app import TuiOS
    from tui_os.services.vault import VaultClient, VaultConfig

    vault = VaultClient(VaultConfig(url="https://vault.invalid", email="a@b"))
    b = TerminalBridge(
        lambda dc: TuiOS(Settings(demo_mode=True, telemetry_interval=1.0), driver_class=dc,
                         vault_client=vault),
        cols=147,
        rows=46,
    )
    b.start()
    try:
        assert b.ready.wait(10), b.error
        assert _wait_for(lambda: "Unlock vault" in screen(b))
        secret = "correct horse battery staple, with ünicode & a $ign"
        assert b.send_paste(secret + "\r\n")
        field = lambda: b.app.screen.query_one("#unlock-password", Input)  # noqa: E731
        assert _wait_for(lambda: field().value == secret)
        assert secret not in screen(b)  # still masked
        assert not b.send_paste("")
        b.send_key("escape", "\x1b")
        assert _wait_for(lambda: "Unlock vault" not in screen(b))
    finally:
        b.stop()
        assert b.finished.wait(5)
    assert b.error is None, b.error


def test_key_bar_matches_footer_and_taps_run_keys(bridge):
    # The bar changes once focus lands in the prompt ("esc Cancel" appears
    # first, shifting every key): tap only what the settled bar shows.
    assert _wait_for(lambda: bridge.text_lines()[-1].startswith("esc Cancel"))
    last = bridge.text_lines()[-1]
    assert "^b Telemetry" in last and "^2 Mail" in last and "^r Redraw" in last
    bridge.tap(last.index("^2 Mail") + 1, len(bridge.text_lines()) - 1)
    assert _wait_for(lambda: "NeoMutt" in screen(bridge))


def test_ctrl_shift_c_puts_the_screen_on_the_host_clipboard(bridge):
    """App.copy_to_clipboard's OSC 52 reaches the host (Kivy clipboard on the Boox)."""
    import threading

    got: list[str] = []
    done = threading.Event()
    bridge.on_clipboard = lambda text: (got.append(text), done.set())
    time.sleep(0.5)  # let the first screen settle
    bridge.send_key("ctrl+shift+c", "\x03")
    assert done.wait(5)
    assert got[0].strip()
