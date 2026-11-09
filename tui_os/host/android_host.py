"""HostServices for the APK: the native WebView via pyjnius.

Java side: ``android/src/org/olares/tuios/EinkBrowser.java`` (added to the
build with ``android.add_src``). Its callbacks arrive on the Android UI
thread and are relayed to the Textual loop through the bridge, which is
thread-safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jnius import PythonJavaClass, autoclass, java_method  # type: ignore[import-not-found]

from .eink_web import EINK_CSS, EINK_JS
from .services import Region, WebViewEvent

if TYPE_CHECKING:
    from .bridge import TerminalBridge
    from .kivy_app import TerminalView

#: Keys the page hands back; forwarded to Textual as these key names.
_FORWARD = {"f1", "f2", "f3", "f4", "f5", "f6", "ctrl+b", "ctrl+l", "ctrl+q"}


class _Listener(PythonJavaClass):
    __javainterfaces__ = ["org/olares/tuios/EinkBrowser$Listener"]
    __javacontext__ = "app"

    def __init__(self, bridge: "TerminalBridge") -> None:
        super().__init__()
        self.bridge = bridge

    @java_method("(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V")
    def onPageEvent(self, kind, url, title):  # noqa: N802 - Java interface
        event = WebViewEvent(str(kind), str(url or ""), str(title or ""))
        self.bridge.call_in_app(lambda app: app.post_message(event))

    @java_method("(Ljava/lang/String;)V")
    def onKey(self, key):  # noqa: N802 - Java interface
        key = str(key)
        if key == "escape":
            event = WebViewEvent("released")
            self.bridge.call_in_app(lambda app: app.post_message(event))
        elif key in _FORWARD:
            self.bridge.send_key(key)


class AndroidHost:
    webview_available = True

    def __init__(self, bridge: "TerminalBridge", view: "TerminalView") -> None:
        self.bridge = bridge
        self.view = view
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        EinkBrowser = autoclass("org.olares.tuios.EinkBrowser")
        self._listener = _Listener(bridge)  # keep a reference: Java holds a weak one
        self._browser = EinkBrowser(activity, self._listener)
        self._browser.setUserStyle(EINK_CSS, EINK_JS)
        self._region: Region | None = None

    def _pixels(self, region: Region) -> tuple[int, int, int, int]:
        """Cells → window pixels (top-left origin), on the same snapped edges."""
        x, y, w, h = region
        g = self.view.grid
        left = int(self.view.x) + g.col_edge(x)
        right = int(self.view.x) + g.col_edge(x + w)
        top = int(self.view.parent_top_offset()) + g.row_edge(y)
        bottom = int(self.view.parent_top_offset()) + g.row_edge(y + h)
        return left, top, right - left, bottom - top

    def show_webview(self, region: Region) -> None:
        self._region = region
        self._browser.show(*self._pixels(region))

    def relayout(self) -> None:
        """Window/grid geometry changed: re-place the view on the same cells."""
        if self._region is not None:
            self._browser.show(*self._pixels(self._region))

    def hide_webview(self) -> None:
        self._region = None
        self._browser.hide()

    def load_url(self, url: str) -> None:
        self._browser.load(url)

    def load_html(self, html: str, base_url: str) -> None:
        self._browser.loadHtml(html, base_url)

    def go_back(self) -> None:
        self._browser.back()

    def reload(self) -> None:
        self._browser.reload()

    def focus_webview(self) -> None:
        self._browser.focus()

    def destroy(self) -> None:
        self._browser.destroy()
