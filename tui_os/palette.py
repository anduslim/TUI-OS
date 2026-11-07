"""The six E-ink colour tokens, and a clamp for colours we don't control.

Our own widgets only ever use the tokens. Output from remote programs (k9s
over SSH, anything printed in a terminal pane) can contain arbitrary 256- or
24-bit colours, so :func:`nearest` maps every colour onto the palette before
it reaches the screen. The spec holds even for content we didn't write.
"""

from __future__ import annotations

from functools import lru_cache

BLACK = "#000000"
WHITE = "#FFFFFF"
GREEN = "#00FF00"
BLUE = "#0000FF"
YELLOW = "#FFFF00"
RED = "#FF0000"

TOKENS: tuple[str, ...] = (BLACK, WHITE, GREEN, BLUE, YELLOW, RED)
_RGB = {t: (int(t[1:3], 16), int(t[3:5], 16), int(t[5:7], 16)) for t in TOKENS}

# The 16 ANSI colours, by name as pyte reports them, mapped by intent
# rather than by RGB distance (e.g. ANSI "cyan" is an accent, not grey).
ANSI_NAMES = {
    "black": BLACK, "red": RED, "green": GREEN, "brown": YELLOW, "yellow": YELLOW,
    "blue": BLUE, "magenta": RED, "cyan": GREEN, "white": WHITE,
    "brightblack": WHITE, "brightred": RED, "brightgreen": GREEN, "brightbrown": YELLOW,
    "brightyellow": YELLOW, "brightblue": BLUE, "brightmagenta": RED, "brightcyan": GREEN,
    "brightwhite": WHITE,
}


@lru_cache(maxsize=4096)
def nearest(colour: str) -> str:
    """Closest token to ``colour`` (``#rrggbb``, ``rrggbb`` or an ANSI name)."""
    c = colour.strip().lower()
    if c in ANSI_NAMES:
        return ANSI_NAMES[c]
    c = c.lstrip("#")
    if len(c) != 6:
        return WHITE
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    # Weighted RGB distance (green counts most, as in perceived lightness).
    return min(
        TOKENS,
        key=lambda t: 2 * (r - _RGB[t][0]) ** 2 + 4 * (g - _RGB[t][1]) ** 2 + 3 * (b - _RGB[t][2]) ** 2,
    )


def readable_on(background: str) -> str:
    """Text colour the pairing rules allow on a token background."""
    return {BLUE: WHITE, GREEN: BLACK, RED: WHITE, YELLOW: BLACK, WHITE: BLACK}.get(background, WHITE)
