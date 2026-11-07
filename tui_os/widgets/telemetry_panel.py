"""Left-hand telemetry sidebar.

E-ink notes: each metric row only repaints when its *rendered text* changes.
Values are quantised (whole degrees, 0.1 GB, 20-cell bars) so sensor jitter
does not trigger refreshes.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..services.telemetry import TelemetrySnapshot

BAR_CELLS = 20

LEVEL_STYLES = {
    "ok": "bold #00FF00",
    "warn": "bold #FFFF00",
    "crit": "bold #FFFFFF on #FF0000",
    "none": "#FFFFFF",
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
    if ratio is None:
        return " " * cells
    filled = round(max(0.0, min(1.0, ratio)) * cells)
    # No shade glyphs (░▒▓): they dither into grey noise on E-ink.
    return "█" * filled + "·" * (cells - filled)


def temp_level(temp: float | None, warn: float = 75, crit: float = 85) -> str:
    if temp is None:
        return "none"
    if temp >= crit:
        return "crit"
    if temp >= warn:
        return "warn"
    return "ok"


class MetricRow(Static):
    """Label on one line, value + step bar on the next."""

    DEFAULT_CSS = """
    MetricRow { height: 3; padding: 0 1; }
    """

    def __init__(self, label: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.label = label
        self._last: tuple[str, str, str] | None = None
        self.set_metric("--", None, "none")

    def set_metric(self, value: str, ratio: float | None, level: str) -> bool:
        """Update the row; returns True if anything was repainted."""
        key = (value, bar(ratio), level)
        if key == self._last:
            return False
        self._last = key
        style = LEVEL_STYLES.get(level, LEVEL_STYLES["none"])
        text = Text()
        text.append(f"{self.label}\n", "bold #FFFFFF")
        text.append(f"{value:>12}", style)
        text.append("\n")
        text.append(key[1], style if ratio is not None else "#FFFFFF")
        self.update(text)
        return True


class TelemetryPanel(Vertical):
    """Collapsible sidebar. Toggled by the app (Ctrl+B)."""

    BORDER_TITLE = "OLARES ONE"

    def __init__(self, *, source_label: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.source_label = source_label
        self._last_status = ""

    def compose(self) -> ComposeResult:
        yield MetricRow("CPU TEMP", id="m-cpu-temp")
        yield MetricRow("GPU TEMP", id="m-gpu-temp")
        yield MetricRow("VRAM", id="m-vram")
        yield MetricRow("NET  RX ↓", id="m-net-rx")
        yield MetricRow("NET  TX ↑", id="m-net-tx")
        yield Static("", id="telemetry-status")

    def on_mount(self) -> None:
        self.border_subtitle = "connecting"
        self.query_one("#telemetry-status", Static).update(
            Text(self.source_label, style="#FFFFFF")
        )

    def apply_snapshot(self, snap: TelemetrySnapshot, *, max_net: float) -> None:
        q = self.query_one
        cpu = None if snap.cpu_temp is None else round(snap.cpu_temp)
        gpu = None if snap.gpu_temp is None else round(snap.gpu_temp)
        q("#m-cpu-temp", MetricRow).set_metric(
            "--" if cpu is None else f"{cpu} °C",
            None if cpu is None else cpu / 100,
            temp_level(cpu),
        )
        q("#m-gpu-temp", MetricRow).set_metric(
            "--" if gpu is None else f"{gpu} °C",
            None if gpu is None else gpu / 100,
            temp_level(gpu, warn=78, crit=88),
        )
        ratio = snap.vram_ratio
        if snap.vram_used is None:
            vram_text = "--"
        else:
            used_gb = snap.vram_used / 1024**3
            vram_text = f"{used_gb:.1f}"
            if snap.vram_total:
                vram_text += f"/{snap.vram_total / 1024**3:.0f} GB"
            else:
                vram_text += " GB"
        vram_level = "none" if ratio is None else ("crit" if ratio > 0.95 else "warn" if ratio > 0.85 else "ok")
        q("#m-vram", MetricRow).set_metric(vram_text, ratio, vram_level)

        for row_id, value in (("#m-net-rx", snap.net_rx), ("#m-net-tx", snap.net_tx)):
            q(row_id, MetricRow).set_metric(
                format_bytes(value, per_second=True),
                None if value is None else value / max_net,
                "none" if value is None else "ok",
            )

        if snap.source == "offline":
            subtitle = "OFFLINE"
            status = Text(f"✗ {snap.error or 'unreachable'}\n{self.source_label}", style="bold #FF0000")
        else:
            subtitle = snap.source.upper()
            stamp = time.strftime("%H:%M", time.localtime(snap.timestamp))
            status = Text(f"● {snap.source} @ {stamp}\n{self.source_label}", style="#00FF00")
            if snap.missing:
                status.append(f"\nno data: {', '.join(snap.missing)}", style="#FFFF00")
        if self.border_subtitle != subtitle:
            self.border_subtitle = subtitle
        # Status line carries a minute-resolution stamp: repaints ≤ 1/min.
        if status.plain != self._last_status:
            self._last_status = status.plain
            self.query_one("#telemetry-status", Static).update(status)
