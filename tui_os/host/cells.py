"""A cell grid fed straight from Textual's compositor.

Textual's terminal drivers turn every screen update into ANSI escape
sequences. In the APK there is no terminal: the bridge used to feed that
text into ``pyte`` only to parse it back into cells, which cost more CPU
than rendering the frame in the first place (about half the app thread's
time on a tab switch). :class:`CellGrid` takes the compositor's update
objects instead, which already hold positioned Rich segments, and writes
their text and styles into a plain list of cells.

Pure Python, no Kivy: unit-tested on desktop.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rich.cells import get_character_cell_size
from rich.color import Color, ColorType
from rich.segment import ControlType, Segment
from rich.style import Style

DEFAULT_FG = "FFFFFF"
DEFAULT_BG = "000000"

# ANSI standard colours (0-15) mapped onto the six E-ink design tokens. The
# app emits truecolor, so these only appear if a widget uses ANSI names.
_STANDARD = (
    "000000", "FF0000", "00FF00", "FFFF00", "0000FF", "FF0000", "00FF00", "FFFFFF",
    "000000", "FF0000", "00FF00", "FFFF00", "0000FF", "FF0000", "00FF00", "FFFFFF",
)

#: (fg, bg, bold, underline, italic)
StyleKey = tuple[str, str, bool, bool, bool]
DEFAULT_KEY: StyleKey = (DEFAULT_FG, DEFAULT_BG, False, False, False)
BLANK = (" ", DEFAULT_KEY)


@dataclass(frozen=True)
class Run:
    """A horizontal span of cells sharing one style."""

    col: int
    text: str
    fg: str  # RRGGBB
    bg: str  # RRGGBB
    bold: bool = False
    underline: bool = False
    italic: bool = False


def _hex(color: Color | None, default: str) -> str:
    if color is None or color.type == ColorType.DEFAULT:
        return default
    if color.type == ColorType.STANDARD and color.number is not None:
        return _STANDARD[color.number]
    r, g, b = color.get_truecolor()
    return f"{r:02X}{g:02X}{b:02X}"


_STYLE_CACHE: dict[Style | None, StyleKey] = {}


def style_key(style: Style | None) -> StyleKey:
    key = _STYLE_CACHE.get(style)
    if key is None:
        if style is None:
            key = DEFAULT_KEY
        else:
            fg, bg = _hex(style.color, DEFAULT_FG), _hex(style.bgcolor, DEFAULT_BG)
            if style.reverse:
                fg, bg = bg, fg
            key = (fg, bg, bool(style.bold), bool(style.underline), bool(style.italic))
        if len(_STYLE_CACHE) > 4096:
            _STYLE_CACHE.clear()
        _STYLE_CACHE[style] = key
    return key


class CellGrid:
    """``rows`` × ``cols`` cells of ``(char, StyleKey)``.

    The right half of a wide character holds ``""``, as in pyte.
    """

    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.cells: list[list[tuple[str, StyleKey]]] = [[BLANK] * cols for _ in range(rows)]
        #: Rows written since the last :meth:`take_dirty`.
        self.dirty: set[int] = set(range(rows))

    # ------------------------------------------------------------- geometry
    def resize(self, cols: int, rows: int) -> None:
        """Keep the overlapping content; Textual repaints after a resize."""
        old = self.cells
        self.cells = [
            (old[y][:cols] + [BLANK] * max(0, cols - len(old[y]))) if y < len(old) else [BLANK] * cols
            for y in range(rows)
        ]
        self.cols, self.rows = cols, rows
        self.dirty = set(range(rows))

    # -------------------------------------------------------------- writing
    def apply(self, update: Any) -> None:
        """Apply a Textual ``CompositorUpdate`` (layout or chops update)."""
        from textual._compositor import ChopsUpdate, LayoutUpdate

        if isinstance(update, LayoutUpdate):
            x0, y = update.region.x, update.region.y
            for line in update.strips:
                x = x0
                for strip in line:
                    x = self.put_segments(y, x, strip)
                y += 1
        elif isinstance(update, ChopsUpdate):
            # Mirrors ChopsUpdate.render_segments, minus the escape codes.
            chops, chop_ends = update.chops, update.chop_ends
            for y, x1, x2 in update.spans:
                for end, (x, strip) in zip(chop_ends[y], chops[y].items()):
                    if strip is None or x > x2 or end <= x1:
                        continue
                    if not (x2 > x >= x1 and end <= x2):
                        strip = strip.crop(0, min(end, x2) - x)
                    self.put_segments(y, x, strip)
        else:
            self.put_positioned(update.__rich_console__(None, None))  # type: ignore[arg-type]

    def put_positioned(self, segments: Iterable[Segment]) -> None:
        """Segments with cursor-move controls (any Rich update renderable)."""
        x = y = 0
        for seg in segments:
            if seg.control:
                for control in seg.control:
                    if control[0] == ControlType.CURSOR_MOVE_TO:
                        x, y = control[1], control[2]
                continue
            if seg.text == "\n":
                x, y = 0, y + 1
                continue
            x = self.put_segments(y, x, (seg,))

    def put_segments(self, y: int, x: int, segments: Iterable[Segment]) -> int:
        """Write segments into row ``y`` from column ``x``; returns the end column."""
        if not (0 <= y < self.rows and 0 <= x < self.cols):
            return x  # off screen, e.g. a frame laid out before a resize
        row, cols = self.cells[y], self.cols
        start = x
        for text, style, control in segments:
            if control or not text:
                continue
            key = style_key(style)
            if text.isascii():
                n = min(len(text), cols - x)
                if n > 0:
                    row[x:x + n] = [(ch, key) for ch in text[:n]]
                x += len(text)
                continue
            for ch in text:
                width = get_character_cell_size(ch)
                if width == 0:
                    continue  # combining mark: drop rather than misalign
                if x >= cols:
                    break
                row[x] = (ch, key)
                if width == 2:
                    if x + 1 < cols:
                        row[x + 1] = ("", key)
                x += width
        if x > start:
            self._repair_wide(row, start, min(x, cols))
            self.dirty.add(y)
        return x

    def _repair_wide(self, row: list[tuple[str, StyleKey]], x0: int, x1: int) -> None:
        """Blank wide characters cut in half by a write to [x0, x1)."""
        if x0 > 0:
            left = row[x0 - 1][0]
            if left and not left.isascii() and get_character_cell_size(left) == 2:
                row[x0 - 1] = (" ", row[x0 - 1][1])  # its right half was overwritten
        if x1 < self.cols and row[x1][0] == "":
            row[x1] = (" ", row[x1][1])  # its left half was overwritten

    # -------------------------------------------------------------- reading
    def row_runs(self, y: int) -> list[Run]:
        runs: list[Run] = []
        cur_key: StyleKey | None = None
        start = 0
        chars: list[str] = []
        for x, (ch, key) in enumerate(self.cells[y]):
            if ch == "":  # right half of a wide character
                continue
            if key != cur_key:
                if cur_key is not None:
                    runs.append(Run(start, "".join(chars), *cur_key))
                cur_key, start, chars = key, x, []
            chars.append(ch)
        if cur_key is not None:
            runs.append(Run(start, "".join(chars), *cur_key))
        return runs

    def text_lines(self) -> list[str]:
        return ["".join(ch for ch, _ in row) for row in self.cells]
