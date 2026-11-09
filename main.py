"""APK entry point (python-for-android runs main.py from the app root).

Starts the Kivy host, which embeds the Textual app in-process. There is no
terminal, shell or Termux involved; see tui_os/host/.

If anything fails before the UI exists (an import, the host itself), the
traceback is shown on screen and written to crash.log in the app's private
storage, instead of the app silently closing.
"""

import os
import traceback

# Before any Textual import: 24-bit colour into the embedded terminal.
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
os.environ.setdefault("COLORTERM", "truecolor")


def _fallback_crash_screen(text: str) -> None:
    print(text)  # logcat, tag "python"
    path = os.path.join(os.environ.get("ANDROID_PRIVATE", "."), "crash.log")
    try:
        with open(path, "w") as fh:
            fh.write(text)
    except OSError:
        pass
    from kivy.app import App
    from kivy.core.window import Window
    from kivy.uix.label import Label

    class CrashApp(App):
        def build(self):
            Window.clearcolor = (0, 0, 0, 1)
            label = Label(text=f"TUI-OS failed to start. Saved to {path}\n\n{text}",
                          color=(1, 1, 1, 1), halign="left", valign="top", font_size="14sp")
            label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            return label

    CrashApp().run()


if __name__ == "__main__":
    try:
        from tui_os.host.kivy_app import main
    except Exception:  # noqa: BLE001 - show any startup failure on the panel
        _fallback_crash_screen(traceback.format_exc())
    else:
        main()
