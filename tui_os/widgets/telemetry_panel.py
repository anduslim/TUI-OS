"""Left-hand telemetry sidebar (30 columns, collapsible with Ctrl+B).

Layout, inside the ``OLARES ONE`` titled border::

    CPU TEMP              50 °C      line 1: label + value
    ██████████··········             line 2: 20-cell block meter
                                     (one blank row between metrics)
    ...
    ─────────────────────────
    OLLAMA                ready      the local LLM node
    qwen2.5-coder:14b
    ...
    ● prometheus @ 18:03             source status (bottom)
    ──────────────────── LIVE ──     LIVE / OFFLINE in the bottom border

Panel states: ``ok`` (green/white), ``warn`` (any metric at warn/crit:
yellow / white-on-red) and ``offline`` (source unreachable).

E-ink: every row repaints only when its rendered text changes. Values are
quantised first (whole °C, 0.1 GB, 5 % meter steps) so sensor jitter never
causes a refresh.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..services.telemetry import TelemetrySnapshot

BAR_CELLS = 20
#: Usable columns inside the 30-column panel (2 border + 2 padding).
INNER_COLS = 26

LEVEL_STYLES = {
    "ok": "bold #00FF00",
    "warn": "bold #FFFF00",
    "crit": "bold #FFFFFF on #FF0000",
    "none": "#FFFFFF",
}

LLM_STYLES = {
    "ready": "bold #00FF00",
    "generating": "bold #00FF00",
    "connecting": "#FFFFFF",
    "cancelled": "bold #FFFF00",
    "not pulled": "bold #FFFF00",
    "unreachable": "bold #FF0000",
    "error": "bold #FFFFFF on #FF0000",
}


def format_bytes(value: float | None, per_second: bool = False) -> str:
    if value is None:
        return "--"
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(value)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    text = f"{v:.0f}" if i == 0 or v >= 100 else f"{v:.1f}"
    return f"{text} {units[i]}{'/s' if per_second else ''}"


def bar(ratio: float | None, cells: int = BAR_CELLS) -> str:
    """20-cell meter: solid █ then tracking dots ·. Never ░▒▓ (grey dither)."""
    if ratio is None:
        return " " * cells
    filled = round(max(0.0, min(1.0, ratio)) * cells)
    return "█" * filled + "·" * (cells - filled)


def temp_level(temp: float | None, warn: float = 75, crit: float = 85) -> str:
    if temp is None:
        return "none"
    if temp >= crit:
        return "crit"
    if temp >= warn:
        return "warn"
    return "ok"


def ratio_level(ratio: float | None, warn: float = 0.85, crit: float = 0.95) -> str:
    if ratio is None:
        return "none"
    return "crit" if ratio > crit else "warn" if ratio > warn else "ok"


def two_column(left: str, right: str, width: int = INNER_COLS) -> tuple[str, str]:
    """Pad so ``left`` hugs the left edge and ``right`` the right edge."""
    gap = max(1, width - len(left) - len(right))
    return left, " " * gap + right


class MetricRow(Static):
    """Two-line metric: label + value, then the block meter."""

    DEFAULT_CSS = """
    MetricRow { height: 2; margin: 0 0 1 0; }
    """

    def __init__(self, label: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.label = label
        self._last: tuple[str, str, str] | None = None
        self.level = "none"
        self.set_metric("--", None, "none")

    def set_metric(self, value: str, ratio: float | None, level: str) -> bool:
        """Update the row; returns True only if the rendered text changed."""
        key = (value, bar(ratio), level)
        if key == self._last:
            return False
        self._last = key
        self.level = level
        style = LEVEL_STYLES.get(level, LEVEL_STYLES["none"])
        left, right = two_column(self.label, value)
        text = Text()
        text.append(left, "bold #FFFFFF")
        text.append(right, style)
        text.append("\n")
        text.append(key[1], style if ratio is not None else "#FFFFFF")
        self.update(text)
        return True


class LlmNode(Static):
    """Model name and processing state of the Ollama server."""

    DEFAULT_CSS = """
    LlmNode { height: 4; border-top: solid #FFFFFF; padding: 1 0 0 0; }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._last: tuple[str, str] | None = None
        self.state = "connecting"
        self.model = ""

    def set_state(self, model: str, state: str) -> bool:
        if (model, state) == self._last:
            return False
        self._last = (model, state)
        self.model, self.state = model, state
        left, right = two_column("OLLAMA", state)
        text = Text()
        text.append(left, "bold #FFFFFF")
        text.append(right, LLM_STYLES.get(state, "#FFFFFF"))
        text.append("\n")
        text.append(model[:INNER_COLS], "#FFFFFF")
        self.update(text)
        return True


class TelemetryPanel(Vertical):
    """Collapsible sidebar. Toggled by the app (Ctrl+B)."""

    BORDER_TITLE = "OLARES ONE"

    def __init__(self, *, source_label: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.source_label = source_label
        self.state = "offline"
        self._last_status = ""

    def compose(self) -> ComposeResult:
        yield MetricRow("CPU TEMP", id="m-cpu-temp")
        yield MetricRow("GPU TEMP", id="m-gpu-temp")
        yield MetricRow("VRAM", id="m-vram")
        yield MetricRow("NET RX ↓", id="m-net-rx")
        yield MetricRow("NET TX ↑", id="m-net-tx")
        yield LlmNode(id="llm-node")
        yield Static("", id="telemetry-status")

    def on_mount(self) -> None:
        self.border_subtitle = "OFFLINE"
        self.set_class(True, "-offline")
        self.query_one("#telemetry-status", Static).update(
            Text(f"○ connecting\n{self.source_label}", style="#FFFFFF")
        )

    def set_llm(self, model: str, state: str) -> None:
        self.query_one(LlmNode).set_state(model, state)

    def apply_snapshot(self, snap: TelemetrySnapshot, *, max_net: float) -> str:
        """Render a snapshot; returns the panel state (ok / warn / offline)."""
        q = self.query_one
        cpu = None if snap.cpu_temp is None else round(snap.cpu_temp)
        gpu = None if snap.gpu_temp is None else round(snap.gpu_temp)
        rows = q("#m-cpu-temp", MetricRow), q("#m-gpu-temp", MetricRow), q("#m-vram", MetricRow)
        rows[0].set_metric(
            "--" if cpu is None else f"{cpu} °C",
            None if cpu is None else cpu / 100,
            temp_level(cpu),
        )
        rows[1].set_metric(
            "--" if gpu is None else f"{gpu} °C",
            None if gpu is None else gpu / 100,
            temp_level(gpu, warn=78, crit=88),
        )
        ratio = snap.vram_ratio
        if snap.vram_used is None:
            vram_text = "--"
        else:
            vram_text = f"{snap.vram_used / 1024**3:.1f}"
            vram_text += f"/{snap.vram_total / 1024**3:.0f} GB" if snap.vram_total else " GB"
        rows[2].set_metric(vram_text, ratio, ratio_level(ratio))

        for row_id, value in (("#m-net-rx", snap.net_rx), ("#m-net-tx", snap.net_tx)):
            q(row_id, MetricRow).set_metric(
                format_bytes(value, per_second=True),
                None if value is None else value / max_net,
                "none" if value is None else "ok",
            )

        if snap.source == "offline":
            state = "offline"
            status = Text(f"✗ {snap.error or 'unreachable'}\n{self.source_label}", style="bold #FF0000")
        else:
            state = "warn" if any(r.level in ("warn", "crit") for r in rows) else "ok"
            stamp = time.strftime("%H:%M", time.localtime(snap.timestamp))
            status = Text(f"● {snap.source} @ {stamp}\n{self.source_label}", style="#00FF00")
            if snap.missing:
                status.append(f"\nno data: {', '.join(snap.missing)}", style="#FFFF00")

        if state != self.state:
            self.state = state
            self.border_subtitle = "OFFLINE" if state == "offline" else "LIVE"
            for name in ("ok", "warn", "offline"):
                self.set_class(name == state, f"-{name}")
        # The status line carries a minute-resolution stamp: ≤ 1 repaint/min.
        if status.plain != self._last_status:
            self._last_status = status.plain
            self.query_one("#telemetry-status", Static).update(status)
        return state
