"""Glyph atlas: every character rendered once, into one shared texture.

The view used to render each styled text run as its own texture (a
``CoreLabel`` per run) and draw it as its own rectangle. A tab switch shows
mostly new text, so it rendered hundreds of new labels in one frame on the
UI thread, and the canvas grew to thousands of instructions that Kivy
redraws on every frame. That is what made Android show "app isn't
responding".

Here each (character, bold) pair is rendered once, in white, into a slot of
one big texture. A row is then drawn as a few :class:`~kivy.graphics.Mesh`
instructions (one per colour, tinted by a ``Color``) whose quads point at
those slots, so a cache miss costs one glyph, not one run, and after a few
screens nothing new is rendered at all.
"""

from __future__ import annotations

from kivy.core.text import Label as CoreLabel
from kivy.graphics.texture import Texture

ATLAS_W, ATLAS_H = 1024, 2048


class _Capture:
    """Stands in for a label's texture to catch the rendered pixels."""

    data = None

    def blit_data(self, data) -> None:
        self.data = data


class AtlasFull(Exception):
    """No slot left; the caller resets the atlas and repaints everything."""


class GlyphAtlas:
    def __init__(self, font_px: float, fonts: dict[bool, str]) -> None:
        self.font_px = font_px
        self.fonts = fonts
        self.texture = Texture.create(size=(ATLAS_W, ATLAS_H), colorfmt="rgba")
        self.texture.mag_filter = "nearest"
        self.texture.min_filter = "nearest"
        self.texture.blit_buffer(bytes(ATLAS_W * ATLAS_H * 4), colorfmt="rgba", bufferfmt="ubyte")
        #: (char, bold) -> (w, h, u0, v0, u1, v1), or None for blank glyphs.
        self._glyphs: dict[tuple[str, bool], tuple | None] = {}
        self._x = self._y = self._shelf_h = 0
        #: Glyphs rendered since the last check (for the paint budget).
        self.misses = 0

    def __contains__(self, key: tuple[str, bool]) -> bool:
        return key in self._glyphs

    def get(self, ch: str, bold: bool):
        key = (ch, bold)
        try:
            return self._glyphs[key]
        except KeyError:
            pass
        glyph = self._glyphs[key] = self._render(ch, bold)
        self.misses += 1
        return glyph

    def _pixels(self, ch: str, bold: bool) -> tuple[int, int, bytes] | None:
        label = CoreLabel(text=ch, font_size=self.font_px, font_name=self.fonts[bold], color=(1, 1, 1, 1))
        try:
            # First pass lays the text out; the second renders it. Catch the
            # pixels instead of letting the label make its own texture.
            w, h = label.render()
            if w <= 1 or h <= 1:
                return None
            label._size = (w, h)
            capture = _Capture()
            label.texture = capture
            label._render_real()
            data = capture.data
            if data is None:
                return None
            return data.width, data.height, bytes(data.data)
        except Exception:  # noqa: BLE001 - a Kivy internal changed: use the public API
            label = CoreLabel(text=ch, font_size=self.font_px, font_name=self.fonts[bold], color=(1, 1, 1, 1))
            label.refresh()
            tex = label.texture
            if tex is None or tex.width <= 1:
                return None
            # Texture.pixels is bottom-up; flip to top-down like the fast path.
            w, h, raw = tex.width, tex.height, tex.pixels
            stride = w * 4
            return w, h, b"".join(raw[r * stride:(r + 1) * stride] for r in range(h - 1, -1, -1))

    def _render(self, ch: str, bold: bool):
        if ch.isspace():
            return None
        px = self._pixels(ch, bold)
        if px is None:
            return None
        w, h, data = px
        if self._x + w > ATLAS_W:
            self._x, self._y, self._shelf_h = 0, self._y + self._shelf_h + 1, 0
        if self._y + h > ATLAS_H:
            raise AtlasFull
        x, y = self._x, self._y
        # Rows arrive top-down; GL puts the first row at the bottom, so the
        # glyph lands upside down and the texture coordinates flip it back.
        self.texture.blit_buffer(data, pos=(x, y), size=(w, h), colorfmt="rgba", bufferfmt="ubyte")
        self._x += w + 1
        self._shelf_h = max(self._shelf_h, h)
        return (w, h, x / ATLAS_W, y / ATLAS_H, (x + w) / ATLAS_W, (y + h) / ATLAS_H)
