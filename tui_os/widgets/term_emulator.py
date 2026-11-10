"""A terminal emulator widget for full-screen remote programs (k9s over SSH).

* ``pyte`` parses the remote byte stream into a cell grid sized exactly
  to this widget's content region. :class:`Resized` tells the session so it
  can send the window change to the remote PTY, so output never clips.
* Every colour is clamped to the six E-ink tokens and to the pairing rules
  (blue/red fills take white text, green/yellow fills take black, no blue
  text on black). Remote programs cannot break the style guide.
* Only rows whose rendered content changed are repainted. A program that
  redraws the whole screen every refresh tick costs nothing on E-ink if
  the data didn't change.
* Keys go to the remote side as xterm sequences, except a few app-level
  escape hatches (see :attr:`TerminalEmulator.RESERVED`).
* Coding agents (Claude Code, opencode) work too: device-attribute and
  cursor-position queries are answered, pastes are bracketed when the
  program asks for it, and ``CSI >``/``<``/``=`` sequences (kitty keyboard,
  modifyOtherKeys, XTVERSION) are dropped instead of being misread by pyte
  as colour changes (``CSI > 4;1 m`` would turn on bold + underline).
* DEC line drawing (``ESC ( 0``, SO/SI) is honoured, so tmux borders
  are drawn as lines, not ``qqqq``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pyte
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.geometry import Region
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from ..palette import BLACK, BLUE, WHITE, nearest, readable_on

#: pyte stores private mode n as n << 5; DECCKM (application cursor keys) is 1.
DECCKM = 1 << 5

_FIXED = {
    "enter": "\r", "escape": "\x1b", "tab": "\t", "shift+tab": "\x1b[Z",
    "backspace": "\x7f", "delete": "\x1b[3~", "insert": "\x1b[2~",
    "pageup": "\x1b[5~", "pagedown": "\x1b[6~", "space": " ",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
    "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
    "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
}
#: Bracketed paste (``CSI ? 2004 h``), as pyte stores it.
BRACKETED_PASTE = 2004 << 5

#: CSI sequences with a ``<``, ``=`` or ``>`` parameter prefix. pyte has no
#: notion of them and would dispatch e.g. ``CSI > 4;1 m`` as plain SGR.
_PREFIXED_CSI = re.compile(r"\x1b\[[<=>][0-9;:]*[ -/]*[@-~]")
#: An escape sequence cut off at the end of a chunk; held for the next one.
_PARTIAL_ESC = re.compile(r"\x1b(\[[<=>?]?[0-9;:]*[ -/]*)?$")


class _Stream(pyte.Stream):
    """A pyte stream that honours the DEC line-drawing charset.

    pyte ignores ``ESC ( 0`` and SO/SI in UTF-8 mode, so tmux borders, which
    use them (terminfo ``smacs``/``rmacs``), came out as ``qqqq`` and
    ``xxxx``. Like xterm, honour them anyway: the input is already decoded
    text, so leaving UTF-8 mode changes nothing else.
    """

    def __init__(self, screen: pyte.Screen) -> None:
        super().__init__(screen)
        self.use_utf8 = False

    def select_other_charset(self, code: str) -> None:
        """Ignore ``ESC % G``/``ESC % @``: they would turn line drawing off again."""


class _Screen(pyte.Screen):
    """A pyte screen whose replies to terminal queries reach the program."""

    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self.reply: Callable[[str], None] | None = None

    def write_process_input(self, data: str) -> None:
        if self.reply is not None:
            self.reply(data)


_CURSOR = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F"}
_MOD_CODE = {"shift": 2, "alt": 3, "shift+alt": 4, "ctrl": 5, "ctrl+shift": 6, "ctrl+alt": 7}


#: Ctrl + punctuation: the C0 control an xterm sends (Ctrl+Space is NUL,
#: tmux/emacs "set mark"; Ctrl+/ is undo in many editors).
_CTRL_PUNCT = {
    "space": "\x00", "@": "\x00", "`": "\x00",
    "[": "\x1b", "left_square_bracket": "\x1b",
    "\\": "\x1c", "backslash": "\x1c",
    "]": "\x1d", "right_square_bracket": "\x1d",
    "^": "\x1e", "circumflex_accent": "\x1e",
    "_": "\x1f", "underscore": "\x1f", "/": "\x1f", "slash": "\x1f", "-": "\x1f", "minus": "\x1f",
}


def encode_key(key: str, character: str | None, *, app_cursor: bool = False) -> str | None:
    """Translate a Textual key event into the bytes an xterm would send."""
    if key in _FIXED:
        return _FIXED[key]
    *mods, base = key.split("+")
    if base in _CURSOR:
        if mods:
            code = _MOD_CODE.get("+".join(sorted(mods, key=["ctrl", "shift", "alt"].index)))
            return f"\x1b[1;{code}{_CURSOR[base]}" if code else None
        return ("\x1bO" if app_cursor else "\x1b[") + _CURSOR[base]
    if "alt" in mods:
        # Meta: ESC, then the key without Alt (Alt+W is tmux/emacs "copy",
        # Alt+Enter, Ctrl+Alt+X ...). The host may leave out the character.
        rest = "+".join([m for m in mods if m != "alt"] + [base])
        inner = encode_key(rest, character, app_cursor=app_cursor)
        return "\x1b" + inner if inner else None
    if mods == ["ctrl"]:
        if len(base) == 1 and base.isalpha():
            return chr(ord(base.lower()) - 96)
        if base in _CTRL_PUNCT:
            return _CTRL_PUNCT[base]
    if character and character.isprintable():
        return character
    if len(base) == 1 and base.isprintable() and mods in ([], ["shift"]):
        return base.upper() if mods else base
    return None


def cell_style(fg: str, bg: str, *, bold: bool, underline: bool, reverse: bool) -> tuple[str, str, bool, bool]:
    """Clamp a pyte cell's colours to the palette and the pairing rules."""
    f = WHITE if fg == "default" else nearest(fg)
    b = BLACK if bg == "default" else nearest(bg)
    if reverse:
        f, b = b, f
    if b != BLACK:
        f = readable_on(b)  # blue/red → white text, green/yellow/white → black
    elif f in (BLACK, BLUE):
        f = WHITE  # no invisible text, no blue text on black
    return f, b, bold, underline


class TerminalEmulator(Widget, can_focus=True):
    """Renders a pyte screen; forwards keys via ``on_input``."""

    DEFAULT_CSS = """
    TerminalEmulator { background: #000000; color: #FFFFFF; }
    """

    #: Keys the app keeps even while a remote program has focus: tab
    #: switching, the telemetry toggle, redraw and quit.
    RESERVED = frozenset({"f1", "f2", "f3", "f4", "f5", "f6", "ctrl+b", "ctrl+r", "ctrl+q",
                          "ctrl+1", "ctrl+2", "ctrl+3", "ctrl+4", "ctrl+5", "ctrl+6"})

    class Resized(Message):
        def __init__(self, emulator: "TerminalEmulator", cols: int, rows: int) -> None:
            super().__init__()
            self.emulator = emulator
            self.cols = cols
            self.rows = rows

    def __init__(self, on_input: Callable[[str], None] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.on_input = on_input
        self._screen = _Screen(80, 24)
        self._screen.reply = self._reply
        self._stream = _Stream(self._screen)
        self._pending = ""
        self._rows: list[tuple] = []
        self._styles: dict[tuple, Style] = {}

    # ----------------------------------------------------------------- data
    @property
    def grid_size(self) -> tuple[int, int]:
        return self._screen.columns, self._screen.lines

    def text_lines(self) -> list[str]:
        return list(self._screen.display)

    def _reply(self, data: str) -> None:
        if self.on_input is not None:
            self.on_input(data)

    def reset(self) -> None:
        self._pending = ""
        self._screen.reset()
        self._rows = []
        self.refresh()

    def feed(self, data: str) -> None:
        """Apply remote output; repaint only rows that really changed."""
        data = self._pending + data
        partial = _PARTIAL_ESC.search(data)
        if partial is not None:
            data, self._pending = data[: partial.start()], data[partial.start():]
        else:
            self._pending = ""
        data = _PREFIXED_CSI.sub("", data)
        if not data:
            return
        cursor_before = self._screen.cursor.y
        self._stream.feed(data)
        dirty = set(self._screen.dirty) | {cursor_before, self._screen.cursor.y}
        self._screen.dirty.clear()
        width = self.size.width
        for y in sorted(dirty):
            if y >= self._screen.lines:
                continue
            row = self._row_key(y)
            if y >= len(self._rows):
                self._rows.extend([()] * (y + 1 - len(self._rows)))
            if self._rows[y] != row:
                self._rows[y] = row
                self.refresh(Region(0, y, width, 1))

    def _row_key(self, y: int) -> tuple:
        line = self._screen.buffer[y]
        cur = self._screen.cursor
        cursor_x = cur.x if (cur.y == y and not cur.hidden and self.has_focus) else -1
        cells = tuple(
            (ch.data, ch.fg, ch.bg, ch.bold, ch.underscore, ch.reverse)
            for ch in (line[x] for x in range(self._screen.columns))
        )
        return (cursor_x, cells)

    # -------------------------------------------------------------- render
    def _style(self, key: tuple) -> Style:
        style = self._styles.get(key)
        if style is None:
            fg, bg, bold, underline = key
            style = Style(color=fg, bgcolor=bg, bold=bold, underline=underline)
            self._styles[key] = style
        return style

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y >= self._screen.lines:
            return Strip([Segment(" " * width, self._style((WHITE, BLACK, False, False)))], width)
        line = self._screen.buffer[y]
        cur = self._screen.cursor
        cursor_x = cur.x if (cur.y == y and not cur.hidden and self.has_focus) else -1
        segments: list[Segment] = []
        run_key: tuple | None = None
        run: list[str] = []
        for x in range(min(width, self._screen.columns)):
            ch = line[x]
            if ch.data == "":
                continue  # right half of a wide character
            key = cell_style(ch.fg, ch.bg, bold=ch.bold, underline=ch.underscore, reverse=ch.reverse)
            if x == cursor_x:
                key = (BLACK, "#00FF00", key[2], key[3])  # static green block cursor
            if key != run_key:
                if run:
                    segments.append(Segment("".join(run), self._style(run_key)))
                run_key, run = key, []
            run.append(ch.data)
        if run:
            segments.append(Segment("".join(run), self._style(run_key)))
        return Strip(segments, width).adjust_cell_length(width, self._style((WHITE, BLACK, False, False)))

    # -------------------------------------------------------------- events
    def on_resize(self, event: events.Resize) -> None:
        cols, rows = max(1, event.size.width), max(1, event.size.height)
        if (cols, rows) != (self._screen.columns, self._screen.lines):
            self._screen.resize(rows, cols)
            self._rows = []
            self.post_message(self.Resized(self, cols, rows))
        self.refresh()

    def on_focus(self) -> None:
        self._rows = []
        self.refresh()

    def on_blur(self) -> None:
        self._rows = []
        self.refresh()

    def check_consume_key(self, key: str, character: str | None) -> bool:
        return key not in self.RESERVED

    def on_key(self, event: events.Key) -> None:
        if event.key in self.RESERVED:
            return
        app_cursor = DECCKM in self._screen.mode
        data = encode_key(event.key, event.character, app_cursor=app_cursor)
        if data is not None and self.on_input is not None:
            self.on_input(data)
        event.stop()
        event.prevent_default()

    def on_paste(self, event: events.Paste) -> None:
        if self.on_input is not None and event.text:
            text = event.text
            if BRACKETED_PASTE in self._screen.mode:
                # Multi-line pastes land in a prompt instead of being run line by line.
                text = "\x1b[200~" + text.replace("\x1b", "") + "\x1b[201~"
            self.on_input(text)
        event.stop()
