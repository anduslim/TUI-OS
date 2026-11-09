"""Agent tab: a coding agent (Claude Code, opencode, ...) on the Olares One.

Replaces the LLM Terminal on tab 6 when ``agent_command`` is set in
config.toml. It is the Shell tab's SSH terminal running that one command,
so the agent gets a real PTY and its full-screen UI (clamped to the E-ink
palette) instead of the LLM Terminal's plain chat. Wrap the command in tmux,
e.g. ``tmux new -A -s agent claude``, and the agent keeps working while the
tablet sleeps; reopening the tab reattaches to it.
"""

from __future__ import annotations

import shlex

from rich.text import Text

from ..config import Settings
from ..services.ssh import SshTarget
from .shell import ShellPane, shell_target


def login_shell(command: str) -> str:
    """Run ``command`` through the user's interactive login shell.

    A command sent over SSH gets a bare shell: no ``.profile`` or ``.bashrc``,
    so ``kubectl`` (and its KUBECONFIG), tmux in ``/usr/local/bin`` or
    ``~/.local/bin`` and shell aliases are missing, and the command exits
    with 127. Typed into the Shell tab, the same command works.
    """
    command = command.strip()
    return f'exec "${{SHELL:-/bin/bash}}" -ilc {shlex.quote(command)}' if command else ""


class AgentPane(ShellPane):
    TITLE = "AGENT"
    FRAME_TITLE = "agent"

    def make_target(self, settings: Settings) -> SshTarget:
        return shell_target(settings, command=login_shell(settings.agent_command))

    @property
    def display_command(self) -> str:
        return self.settings.agent_command

    @property
    def autoconnect(self) -> bool:
        return True

    def help_text(self, connected: bool) -> str:
        if connected:
            return " all keys → agent (Esc, ^c, shift+tab too)   ^1-6 or tap a tab   ^q quit app"
        return " Enter start agent   ^1-6 or tap a tab   ^b telemetry"

    def ended_text(self) -> str:
        return "agent exited"

    def describe(self, text: Text) -> None:
        text.append("Coding agent on the Olares One\n\n", "bold #FFFFFF")

    def idle_footer(self, text: Text) -> None:
        text.append("Tip: agent_command = \"tmux new -A -s agent claude\" keeps the agent "
                    "running while the tablet sleeps; Enter reattaches.", "#FFFFFF")
