"""What a host (the Android APK) offers the Textual app beyond a terminal.

The Textual side only sees this protocol, so the app runs unchanged on a
desktop terminal (``host=None``: text-mode browser) and inside the APK
(native WebView). Regions are in *cells* of the 147×46 grid; the host
converts them to device pixels.
"""

from __future__ import annotations

from typing import Protocol

from textual.message import Message

Region = tuple[int, int, int, int]  # x, y, width, height in cells


class HostServices(Protocol):
    webview_available: bool

    def show_webview(self, region: Region) -> None: ...

    def hide_webview(self) -> None: ...

    def load_url(self, url: str) -> None: ...

    def load_html(self, html: str, base_url: str) -> None: ...

    def go_back(self) -> None: ...

    def reload(self) -> None: ...

    def focus_webview(self) -> None: ...


class WebViewEvent(Message):
    """From the host's WebView.

    kind: ``started`` | ``finished`` | ``error`` | ``navigate`` (an
    ``about:`` link) | ``released`` (the page gave keyboard focus back).
    """

    def __init__(self, kind: str, url: str = "", title: str = "") -> None:
        super().__init__()
        self.kind = kind
        self.url = url
        self.title = title
