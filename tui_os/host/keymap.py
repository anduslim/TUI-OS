"""Translate hardware keyboard events (Kivy/SDL keycodes) to Textual keys.

Pure functions, no Kivy import, so the mapping is unit-tested on desktop.

Printable characters normally arrive through SDL's text-input events, which
honour the keyboard layout. :func:`translate_keydown` still returns a
``CharKey`` for printable keys so the host can fall back to it (US layout)
when no text-input event follows, e.g. if the IME is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass

# SDL / Kivy keycodes for non-printing keys.
SPECIAL_KEYS: dict[int, tuple[str, str | None]] = {
    13: ("enter", "\r"),
    271: ("enter", "\r"),  # keypad enter
    27: ("escape", "\x1b"),  # also the Android BACK key
    8: ("backspace", "\x7f"),
    9: ("tab", "\t"),
    127: ("delete", None),
    273: ("up", None),
    274: ("down", None),
    275: ("right", None),
    276: ("left", None),
    277: ("insert", None),
    278: ("home", None),
    279: ("end", None),
    280: ("pageup", None),
    281: ("pagedown", None),
    **{282 + i: (f"f{i + 1}", None) for i in range(12)},
}

_US_SHIFT = dict(zip("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?'))


@dataclass(frozen=True)
class NamedKey:
    """Send immediately: a special key or a modifier combination."""

    key: str
    character: str | None = None


@dataclass(frozen=True)
class CharKey:
    """A printable character; prefer the matching text-input event if one comes."""

    character: str


def _printable(keycode: int, codepoint: str | None) -> str | None:
    if codepoint and len(codepoint) == 1 and codepoint.isprintable():
        return codepoint
    if 32 <= keycode < 127:
        return chr(keycode)
    return None


def translate_keydown(
    keycode: int, codepoint: str | None, modifiers: list[str] | tuple[str, ...]
) -> NamedKey | CharKey | None:
    mods = set(modifiers)
    ctrl, alt, shift = "ctrl" in mods, "alt" in mods, "shift" in mods

    if keycode in SPECIAL_KEYS:
        name, char = SPECIAL_KEYS[keycode]
        if name == "tab" and shift and not (ctrl or alt):
            return NamedKey("shift+tab")
        prefix = ("ctrl+" if ctrl else "") + ("alt+" if alt else "") + (
            "shift+" if shift and name not in {"enter", "escape", "backspace"} else ""
        )
        return NamedKey(prefix + name, None if prefix else char)

    char = _printable(keycode, codepoint)
    if char is None:
        return None
    base = char.lower()
    if ctrl and base.isalpha() and len(base) == 1:
        return NamedKey(f"ctrl+{base}", chr(ord(base) - 96))
    if alt:
        return NamedKey(f"alt+{base}")
    if shift:
        char = char.upper() if char.isalpha() else _US_SHIFT.get(char, char)
    return CharKey(char)
