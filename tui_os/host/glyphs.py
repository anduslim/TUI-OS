"""Pixel-exact geometry for box-drawing and block-element characters.

Font glyphs for ``│ ─ ┌ █ ▌`` rarely fill a cell exactly, so stacked
borders show hairline gaps, and their anti-aliased edges dither to grey on
E-ink. Like most terminal emulators, the host draws these characters itself
as solid rectangles snapped to the cell's pixel edges.

:func:`cell_rects` is pure (no Kivy) so it is unit-tested on desktop.
"""

from __future__ import annotations

# (left, right, up, down) arm weights: 0 none, 1 light, 2 heavy.
_LINES: dict[str, tuple[int, int, int, int]] = {
    "─": (1, 1, 0, 0), "│": (0, 0, 1, 1),
    "┌": (0, 1, 0, 1), "┐": (1, 0, 0, 1), "└": (0, 1, 1, 0), "┘": (1, 0, 1, 0),
    "╭": (0, 1, 0, 1), "╮": (1, 0, 0, 1), "╰": (0, 1, 1, 0), "╯": (1, 0, 1, 0),
    "├": (0, 1, 1, 1), "┤": (1, 0, 1, 1), "┬": (1, 1, 0, 1), "┴": (1, 1, 1, 0),
    "┼": (1, 1, 1, 1),
    "━": (2, 2, 0, 0), "┃": (0, 0, 2, 2),
    "┏": (0, 2, 0, 2), "┓": (2, 0, 0, 2), "┗": (0, 2, 2, 0), "┛": (2, 0, 2, 0),
    "┣": (0, 2, 2, 2), "┫": (2, 0, 2, 2), "┳": (2, 2, 0, 2), "┻": (2, 2, 2, 0),
    "╋": (2, 2, 2, 2),
    "╴": (1, 0, 0, 0), "╶": (0, 1, 0, 0), "╵": (0, 0, 1, 0), "╷": (0, 0, 0, 1),
    "╸": (2, 0, 0, 0), "╺": (0, 2, 0, 0), "╹": (0, 0, 2, 0), "╻": (0, 0, 0, 2),
}

# Block elements as (x0, y0, x1, y1) fractions of the cell, y measured DOWN
# from the top of the cell.
_BLOCKS: dict[str, tuple[float, float, float, float]] = {
    "█": (0, 0, 1, 1),
    "▀": (0, 0, 1, 0.5), "▄": (0, 0.5, 1, 1),
    "▌": (0, 0, 0.5, 1), "▐": (0.5, 0, 1, 1),
    "▔": (0, 0, 1, 1 / 8), "▕": (7 / 8, 0, 1, 1),
    **{ch: (0, 1 - n / 8, 1, 1) for n, ch in enumerate("▁▂▃▄▅▆▇", start=1)},
    **{ch: (0, 0, n / 8, 1) for n, ch in zip(range(7, 0, -1), "▉▊▋▌▍▎▏")},
}

DRAWN = frozenset(_LINES) | frozenset(_BLOCKS)

Rect = tuple[int, int, int, int]  # x, y_top, width, height (pixels, y down)


def cell_rects(ch: str, x0: int, y0: int, x1: int, y1: int, scale: float) -> list[Rect]:
    """Rectangles covering ``ch`` inside the cell [x0, x1) × [y0, y1).

    Coordinates are pixels with y growing downward (the caller flips them
    for Kivy). ``scale`` is device pixels per logical pixel; light lines are
    1 logical px thick, heavy lines 2.
    """
    w, h = x1 - x0, y1 - y0
    block = _BLOCKS.get(ch)
    if block is not None:
        bx0 = x0 + round(block[0] * w)
        bx1 = x0 + round(block[2] * w)
        by0 = y0 + round(block[1] * h)
        by1 = y0 + round(block[3] * h)
        return [(bx0, by0, max(1, bx1 - bx0), max(1, by1 - by0))]

    arms = _LINES.get(ch)
    if arms is None:
        return []
    light = max(1, round(scale))
    heavy = max(light + 1, round(2 * scale))
    cx, cy = x0 + w // 2, y0 + h // 2
    rects: list[Rect] = []
    left, right, up, down = arms
    # Horizontal arms are centred on cy, vertical arms on cx. Each arm runs
    # past the centre by half the thickest crossing line so corners close.
    span_v = max((light if a == 1 else heavy) for a in (up, down)) if (up or down) else 0
    span_h = max((light if a == 1 else heavy) for a in (left, right)) if (left or right) else 0
    for weight, start, end in ((left, x0, cx + (span_v + 1) // 2), (right, cx - span_v // 2, x1)):
        if weight:
            t = light if weight == 1 else heavy
            rects.append((start, cy - t // 2, max(1, end - start), t))
    for weight, start, end in ((up, y0, cy + (span_h + 1) // 2), (down, cy - span_h // 2, y1)):
        if weight:
            t = light if weight == 1 else heavy
            rects.append((cx - t // 2, start, t, max(1, end - start)))
    return rects
