"""Conformance tests for the E-ink interface spec (style guide v1.0)."""

from __future__ import annotations

import datetime as dt
import time

import pytest
from rich.style import Style

from tui_os.config import Settings
from tui_os.services.telemetry import TelemetrySnapshot
from tui_os.widgets.telemetry_panel import LEVEL_STYLES, INNER_COLS, LlmNode, MetricRow, TelemetryPanel, bar

PALETTE = {"000000", "FFFFFF", "00FF00", "0000FF", "FFFF00", "FF0000"}


class FixedTelemetry:
    def __init__(self, snap: TelemetrySnapshot) -> None:
        self.snap = snap

    async def fetch(self) -> TelemetrySnapshot:
        return self.snap

    async def aclose(self) -> None:
        return None


OK = TelemetrySnapshot(cpu_temp=50, gpu_temp=49, vram_used=9.8 * 1024**3, vram_total=24 * 1024**3,
                       net_rx=1.2e6, net_tx=3.9e5, source="prometheus")
WARN = TelemetrySnapshot(cpu_temp=78, gpu_temp=91, vram_used=23.5 * 1024**3, vram_total=24 * 1024**3,
                         net_rx=4.8e7, net_tx=1.2e6, source="prometheus")
OFFLINE = TelemetrySnapshot(source="offline", error="connection refused")


def _style_key(spec: str):
    st = Style.parse(spec)
    trip = lambda c: None if c is None else tuple(c.get_truecolor())  # noqa: E731
    return trip(st.color), trip(st.bgcolor), bool(st.bold)


def make(snap, **kw):
    from tui_os.app import TuiOS

    from .conftest import ollama_transport
    from tui_os.services.ollama import OllamaClient

    client = OllamaClient("http://o:11434", "qwen2.5-coder:14b",
                          transport=ollama_transport(["hi"], models=("qwen2.5-coder:14b",)))
    return TuiOS(Settings(telemetry_interval=1.0, stream_flush_interval=0.05),
                 telemetry_source=FixedTelemetry(snap), ollama_client=client, **kw)


# ---------------------------------------------------------------- meters
def test_meter_is_exactly_20_cells_of_block_and_dot():
    for ratio in (0.0, 0.33, 0.5, 1.0):
        m = bar(ratio)
        assert len(m) == 20 and set(m) <= {"█", "·"}
    assert not set("".join(bar(r / 10) for r in range(11))) & set("░▒▓")


# ------------------------------------------------------------ panel states
@pytest.mark.parametrize(("snap", "state", "label"), [(OK, "ok", "LIVE"), (WARN, "warn", "LIVE"),
                                                      (OFFLINE, "offline", "OFFLINE")])
async def test_panel_states_and_bottom_label(snap, state, label):
    app = make(snap)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause(0.3)
        panel = app.query_one(TelemetryPanel)
        assert panel.state == state
        assert panel.border_subtitle == label
        assert panel.has_class(f"-{state}")


async def test_metric_rows_are_two_lines_label_value_then_meter():
    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause(0.3)
        row = app.query_one("#m-cpu-temp", MetricRow)
        nxt = app.query_one("#m-gpu-temp", MetricRow)
        assert row.outer_size.height == 2  # label+value, meter
        assert nxt.region.y - row.region.y == 3  # + one blank separator row
        line1, line2 = str(row.render()).split("\n")
        assert line1.startswith("CPU TEMP") and line1.endswith("50 °C") and len(line1) == INNER_COLS
        assert line2 == bar(0.5)
        assert app.query_one(TelemetryPanel).outer_size.width == 30


async def test_warn_state_uses_yellow_and_white_on_red():
    app = make(WARN)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause(0.3)
        assert app.query_one("#m-cpu-temp", MetricRow).level == "warn"
        gpu = app.query_one("#m-gpu-temp", MetricRow)
        assert gpu.level == "crit"
        assert LEVEL_STYLES["warn"] == "bold #FFFF00"
        assert LEVEL_STYLES["crit"] == "bold #FFFFFF on #FF0000"
        assert app.query_one(TelemetryPanel).state == "warn"


async def test_ollama_node_tracks_model_and_state():
    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        node = app.query_one(LlmNode)
        for _ in range(40):
            await pilot.pause(0.05)
            if node.state == "ready":
                break
        assert (node.model, node.state) == ("qwen2.5-coder:14b", "ready")
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.5)
        assert node.state == "ready"  # generating -> ready after the stream
        assert "OLLAMA" in str(node.render())


async def test_ctrl_b_gives_the_30_columns_back_to_the_workspace():
    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        ws = app.query_one("#workspace")
        assert ws.outer_size.width == 147 - 30
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert ws.outer_size.width == 147


# ------------------------------------------------------------------ tabs etc.
async def test_active_tab_is_blue_fill_without_underline():
    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause()
        from textual.widgets._tabs import Underline

        assert all(not u.display for u in app.query(Underline))
        active = app.query_one("#workspace Tab.-active")
        assert active.styles.background.hex.upper() == "#0000FF"
        assert active.styles.color.hex.upper() == "#FFFFFF"


async def test_focused_input_has_heavy_green_border_and_static_cursor():
    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        assert app.focused is prompt and prompt.cursor_blink is False
        kind, colour = prompt.styles.border_top
        assert kind == "heavy" and colour.hex.upper() == "#00FF00"
        await pilot.press("f2")
        await pilot.pause()
        kind, colour = prompt.styles.border_top
        assert kind == "solid" and colour.hex.upper() == "#FFFFFF"


async def test_calendar_today_green_selected_blue():
    from tui_os.widgets.calendar import CalendarPane

    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f3", "l")  # select tomorrow
        await pilot.pause()
        pane = app.query_one(CalendarPane)
        month = pane.query_one("#cal-month").render()
        today, sel = str(dt.date.today().day), str((dt.date.today() + dt.timedelta(days=1)).day)
        styles = {month.plain[s.start:s.end]: str(s.style) for s in month.spans}
        assert _style_key(styles[today]) == _style_key("bold #00FF00")
        if pane.selected.month == dt.date.today().month:
            assert _style_key(styles[sel]) == _style_key("bold #FFFFFF on #0000FF")


# ------------------------------------------------------- whole-screen palette
def test_every_screen_uses_only_the_six_colours():
    """Render all four apps (and both sidebar states) through the APK bridge
    and check every cell's foreground and background."""
    from tui_os.host.bridge import TerminalBridge

    b = TerminalBridge(lambda dc: make(WARN, driver_class=dc), cols=147, rows=46)
    b.start()
    assert b.ready.wait(10), b.error
    try:
        time.sleep(1.0)
        seen: set[str] = set()
        for key in ("f1", "f2", "f3", "f4", "ctrl+b", "f1", "ctrl+b"):
            b.send_key(key)
            time.sleep(0.4)
            b.mark_all_dirty()
            for _y, runs in b.take_dirty_rows():
                for run in runs:
                    seen |= {run.fg, run.bg}
        assert seen <= PALETTE, seen - PALETTE
        assert {"0000FF", "00FF00", "FFFF00", "FF0000"} <= seen
    finally:
        b.stop()


async def test_late_messages_after_teardown_are_ignored():
    """Regression (CI): an LlmState/TelemetryUpdated message can land while
    the screen is being torn down; the handlers must not raise."""
    from tui_os.app import TelemetryUpdated
    from tui_os.widgets.terminal import TerminalPane

    app = make(OK)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.pause()
        await app.query_one(TelemetryPanel).remove()
        app.on_terminal_pane_llm_state(TerminalPane.LlmState("m", "ready"))
        app.on_telemetry_updated(TelemetryUpdated(OK))
