"""APK entry point (python-for-android runs main.py from the app root).

Starts the Kivy host, which embeds the Textual app in-process. There is no
terminal, shell or Termux involved; see tui_os/host/.
"""

import os

# Before any Textual import: 24-bit colour into the embedded terminal.
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
os.environ.setdefault("COLORTERM", "truecolor")

from tui_os.host.kivy_app import main  # noqa: E402

if __name__ == "__main__":
    main()
