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

#: Keys that paste the Android clipboard instead of reaching the TUI as keys.
PASTE_KEYS = frozenset({"ctrl+v", "shift+insert"})

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


class CharFallback:
    """Decides when a printable key press needs the US-layout fallback.

    SDL 2.28+ on Android turns every typed character into THREE events: a
    simulated key-down (from ``commitText``), a text-input event, and the
    real key-down. Waiting for text input after each key-down therefore
    typed most characters twice: the real key-down, arriving after the text
    event, found no text event following it and fired the fallback.

    Once a text-input event has been seen, the platform delivers text that
    way, so key-downs for printable characters are ignored from then on.
    Before that (no IME at all), a key-down with no text event within the
    host's delay is typed from the key itself.
    """

    def __init__(self) -> None:
        self.text_input_seen = False
        self.pending: CharKey | None = None

    def key_down(self, action: CharKey) -> bool:
        """Record a key press; True if the host should schedule :meth:`flush`."""
        if self.text_input_seen:
            self.pending = None
            return False
        self.pending = action
        return True

    def text_input(self) -> None:
        self.text_input_seen = True
        self.pending = None

    def cancel(self) -> None:
        self.pending = None

    def flush(self) -> str | None:
        """The character to type if no text event arrived in time."""
        action, self.pending = self.pending, None
        return action.character if action else None
