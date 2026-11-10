"""Shell tab: a plain SSH terminal on the Olares One.

Opens the user's login shell, or ``shell_command`` when set; for example
``tmux new -A -s main`` attaches to (or creates) a tmux session that
survives the tablet sleeping or the app restarting.

Keyboard: everything goes to the remote side, including Ctrl+B (the tmux
prefix) and Ctrl+R (shell history search). The app keeps only Ctrl+1–6 / F1–F6 (tabs) and
Ctrl+Q; tap a tab label to switch tabs.
"""

from __future__ import annotations

from rich.text import Text

from ..config import Settings
from ..services.ssh import SshTarget
from .ssh_pane import SshPane


def shell_target(settings: Settings, command: str | None = None) -> SshTarget:
    return SshTarget(
        host=settings.resolved_shell_host,
        username=settings.shell_user,
        port=settings.shell_port,
        command=settings.shell_command if command is None else command,
        key_path=settings.resolved_ssh_key_path,
        known_hosts_path=settings.data_dir / "known_hosts",
        mode="shell",
    )


class ShellPane(SshPane):
    TITLE = "SHELL"
    FRAME_TITLE = "shell"
    RESERVED = frozenset({"f1", "f2", "f3", "f4", "f5", "f6", "ctrl+q", "ctrl+shift+c",
                          "ctrl+1", "ctrl+2", "ctrl+3", "ctrl+4", "ctrl+5", "ctrl+6"})

    def make_target(self, settings: Settings) -> SshTarget:
        return shell_target(settings)

    @property
    def autoconnect(self) -> bool:
        return self.settings.shell_autoconnect

    def help_text(self, connected: bool) -> str:
        if connected:
            return " all keys → remote (^b ^r too)   ^1-6 or tap a tab   ^q quit app   exit/^d ends session"
        return " Enter connect   ^1-6 or tap a tab   ^b telemetry"

    def ended_text(self) -> str:
        return "session ended"

    def describe(self, text: Text) -> None:
        text.append("Shell on the Olares One\n\n", "bold #FFFFFF")

    def idle_footer(self, text: Text) -> None:
        text.append("Tip: set shell_command = \"tmux new -A -s main\" in config.toml "
                    "to land in a tmux session that survives disconnects.", "#FFFFFF")
