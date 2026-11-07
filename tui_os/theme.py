"""E-ink dark theme: pure black, pure white, solid green/blue accents.

Colour E-ink (Kaleido) panels quantise colours aggressively and render
greys/tints as dithered noise, so every colour here is a fully saturated
primary and ``luminosity_spread`` is 0 to stop Textual generating shades.
"""

from textual.theme import Theme

BLACK = "#000000"
WHITE = "#FFFFFF"
GREEN = "#00FF00"
BLUE = "#0000FF"
YELLOW = "#FFFF00"
RED = "#FF0000"

EINK_THEME = Theme(
    name="eink-dark",
    primary=GREEN,
    secondary=BLUE,
    accent=BLUE,
    warning=YELLOW,
    error=RED,
    success=GREEN,
    foreground=WHITE,
    background=BLACK,
    surface=BLACK,
    panel=BLACK,
    boost=BLACK,
    dark=True,
    luminosity_spread=0.0,
    text_alpha=1.0,
    variables={
        "block-cursor-foreground": WHITE,
        "block-cursor-background": BLUE,
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-foreground": WHITE,
        "block-cursor-blurred-background": BLACK,
        "block-hover-background": BLACK,
        "input-cursor-background": GREEN,
        "input-cursor-foreground": BLACK,
        "input-selection-background": BLUE,
        "footer-background": BLACK,
        "footer-foreground": WHITE,
        "footer-key-foreground": GREEN,
        "footer-key-background": BLACK,
        "footer-description-foreground": WHITE,
        "footer-description-background": BLACK,
        "footer-item-background": BLACK,
        "scrollbar": WHITE,
        "scrollbar-hover": WHITE,
        "scrollbar-active": GREEN,
        "scrollbar-background": BLACK,
        "scrollbar-background-hover": BLACK,
        "scrollbar-background-active": BLACK,
        "scrollbar-corner-color": BLACK,
        "border": WHITE,
        "border-blurred": WHITE,
        "surface-active": BLACK,
        "foreground-muted": WHITE,
        "foreground-disabled": WHITE,
        "text-muted": WHITE,
        "text-disabled": WHITE,
    },
)
