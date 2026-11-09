"""K9s (Cluster) tab: k9s running on the Olares One over SSH.

Keyboard: every key goes to k9s (arrows, Esc, Enter, ``:`` commands, ``/``
filters, digits for namespace shortcuts, Ctrl combinations) except the
app's own F1–F6, Ctrl+B, Ctrl+R and Ctrl+Q. k9s's Ctrl+B page-up is also
on PgUp.
"""

from __future__ import annotations

from rich.text import Text

from ..config import Settings
from ..services.ssh import SshTarget
from .ssh_pane import SessionFactory, SshPane, TerminalSession  # noqa: F401 - re-exported


def target_from_settings(settings: Settings) -> SshTarget:
    return SshTarget(
        host=settings.resolved_k9s_host,
        username=settings.k9s_user,
        port=settings.k9s_port,
        command=settings.k9s_command,
        kubeconfig=settings.k9s_kubeconfig,
        refresh_rate=settings.k9s_refresh_rate,
        key_path=settings.resolved_ssh_key_path,
        known_hosts_path=settings.data_dir / "known_hosts",
        mode="k9s",
    )


class K9sPane(SshPane):
    TITLE = "K9S (CLUSTER)"
    FRAME_TITLE = "k9s"

    def make_target(self, settings: Settings) -> SshTarget:
        return target_from_settings(settings)

    @property
    def autoconnect(self) -> bool:
        return self.settings.k9s_autoconnect

    def help_text(self, connected: bool) -> str:
        if connected:
            return (" keys → k9s: ↑↓←→ Enter Esc : / 0-9 ?   app: tap tabs  ^b telemetry"
                    "  ^r redraw  ^q quit   ^c quits k9s")
        return " Enter connect   tap a tab to switch   ^b telemetry"

    def ended_text(self) -> str:
        return "k9s exited"

    def describe(self, text: Text) -> None:
        text.append("k9s on the Olares One\n\n", "bold #FFFFFF")

    def idle_footer(self, text: Text) -> None:
        text.append("k9s gets the E-ink skin from ~/.config/tui-os/k9s on the node "
                    "(your own k9s config is untouched).", "#FFFFFF")
