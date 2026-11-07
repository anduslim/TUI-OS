"""The logical cell grid and how it maps onto device pixels.

Spec: one cell is 8.4 × 20 logical px (JetBrains Mono 14/20), and the
workspace is 147 × 46 cells, i.e. 1234.8 × 920 inside the 1240 × 930 logical
viewport. On the Tab Ultra C (2480 × 1860) that is a 2.008× scale: cells of
16.87 × 40.2 device px and a 28.1 px font, whose advance (0.6 em) lands
exactly on the cell width.

Cell edges are rounded to whole pixels, so adjacent cells share an edge
and backgrounds and borders tile without seams or anti-aliased fringes.
"""

from __future__ import annotations

from dataclasses import dataclass

GRID_COLS = 147
GRID_ROWS = 46
CELL_W = 8.4  # logical px
CELL_H = 20.0
FONT_PX = 14.0


@dataclass(frozen=True)
class GridGeometry:
    scale: float
    origin_x: float  # left margin (px) that centres the grid
    origin_y: float  # top margin (px)

    @classmethod
    def fit(cls, width: float, height: float) -> "GridGeometry":
        scale = max(1e-6, min(width / (GRID_COLS * CELL_W), height / (GRID_ROWS * CELL_H)))
        return cls(
            scale=scale,
            origin_x=(width - GRID_COLS * CELL_W * scale) / 2,
            origin_y=(height - GRID_ROWS * CELL_H * scale) / 2,
        )

    @property
    def cell_w(self) -> float:
        return CELL_W * self.scale

    @property
    def cell_h(self) -> float:
        return CELL_H * self.scale

    @property
    def font_px(self) -> float:
        return round(FONT_PX * self.scale, 2)

    def col_edge(self, col: int) -> int:
        """Left pixel edge of column ``col`` (``col == GRID_COLS`` = right edge)."""
        return round(self.origin_x + col * self.cell_w)

    def row_edge(self, row: int) -> int:
        """Top pixel edge of row ``row``, measured down from the widget top."""
        return round(self.origin_y + row * self.cell_h)

    def cell_at(self, x: float, y_down: float) -> tuple[int, int]:
        col = int((x - self.origin_x) // self.cell_w)
        row = int((y_down - self.origin_y) // self.cell_h)
        return min(max(col, 0), GRID_COLS - 1), min(max(row, 0), GRID_ROWS - 1)
