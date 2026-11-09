"""The version shown in the title bar and on the start-up screen.

CI writes ``tui_os/_build_version.py`` with the release number (e.g. 1.1.6)
just before building the APK. A desktop checkout has no such file and
reports ``dev``.
"""

from __future__ import annotations


def app_version() -> str:
    try:
        from ._build_version import VERSION  # type: ignore[import-not-found]
    except ImportError:
        return "dev"
    return str(VERSION).strip() or "dev"
