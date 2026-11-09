"""One-line key bar: Textual's Footer, drawn as a single Static.

Textual's Footer is a container of one widget per key. It recomposes
(unmounts and mounts every key widget, then relayouts the screen) on every
``bindings_updated`` signal, which fires on each focus change, so a tab
switch rebuilt it about five times. On the Boox that was most of a second
of app-thread work per switch. This bar renders the same text, with the same
look (bold green key, white description), as one Text, and only when the set
of shown bindings actually changes. Tapping a key still runs it.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.screen import Screen
from textual.widgets import Static

KEY_STYLE = "bold #00FF00"
DESCRIPTION_STYLE = "#FFFFFF"


class KeyBar(Static):
    DEFAULT_CSS = """
    KeyBar {
        dock: bottom;
        height: 1;
        width: 1fr;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self._signature: tuple | None = None
        self._spans: list[tuple[int, int, str, bool]] = []  # x0, x1, key, enabled
        self._pending = False

    def on_mount(self) -> None:
        self.screen.bindings_updated_signal.subscribe(self, self.bindings_changed)
        self.call_after_refresh(self._render_bindings)

    def on_unmount(self) -> None:
        self.screen.bindings_updated_signal.unsubscribe(self)

    def bindings_changed(self, screen: Screen) -> None:
        if screen is not self.screen or self._pending:
            return
        # A burst of focus changes collapses into one check after the refresh.
        self._pending = True
        self.call_after_refresh(self._render_bindings)

    def _shown(self) -> list[tuple[str, str, str, bool]]:
        """(key, key display, description, enabled), one per action, like Footer."""
        seen: set[str] = set()
        shown = []
        for _node, binding, enabled, _tooltip in self.screen.active_bindings.values():
            if not binding.show or binding.action in seen:
                continue
            seen.add(binding.action)
            shown.append((binding.key, self.app.get_key_display(binding), binding.description, enabled))
        return shown

    def _render_bindings(self) -> None:
        self._pending = False
        if not self.is_attached:
            return
        shown = self._shown()
        signature = tuple(shown)
        if signature == self._signature:
            return
        self._signature = signature
        text = Text(no_wrap=True, overflow="crop")
        spans = []
        for key, display, description, enabled in shown:
            x0 = text.cell_len
            text.append(display, KEY_STYLE if enabled else DESCRIPTION_STYLE)
            text.append(f" {description} " if description else " ", DESCRIPTION_STYLE)
            spans.append((x0, text.cell_len, key, enabled))
        self._spans = spans
        self.update(text, layout=False)  # one fixed row: no relayout

    def key_at(self, x: int) -> str | None:
        for x0, x1, key, enabled in self._spans:
            if x0 <= x < x1:
                return key if enabled else None
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        # Mouse down, as Footer does: a tap runs the key at once, without
        # waiting for Textual to pair it with the release into a Click.
        key = self.key_at(event.x)
        if key is not None:
            event.stop()
            self.app.simulate_key(key.split(",")[0])
