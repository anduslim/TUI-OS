"""Master-password prompt that unlocks Mail/Calendar secrets from Vaultwarden."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ..services.vault import TwoFactorRequired, VaultClient, VaultError

STYLE_BAR = "bold #FFFFFF on #0000FF"


class UnlockScreen(ModalScreen[dict[str, str] | None]):
    """Returns the decrypted item values, or None if skipped (Esc)."""

    BINDINGS = [Binding("escape", "skip", "Skip", priority=True)]

    def __init__(self, client: VaultClient) -> None:
        super().__init__()
        self.client = client
        self.busy = False

    def compose(self) -> ComposeResult:
        cfg = self.client.config
        with Vertical(id="unlock-box"):
            yield Static(Text(" Unlock vault".ljust(200), STYLE_BAR), classes="bar")
            yield Static(f"{cfg.email} @ {self.client.base}\nItem: {cfg.item}\n", id="unlock-info")
            yield Input(placeholder="Master password", password=True, id="unlock-password")
            yield Input(placeholder="Two-step login code", id="unlock-code")
            yield Static("Enter: unlock   Esc: skip (use config.toml)", id="unlock-status")

    def on_mount(self) -> None:
        self.query_one("#unlock-code").display = False
        self.query_one("#unlock-password").focus()

    @on(Input.Submitted)
    def _submit(self) -> None:
        password = self.query_one("#unlock-password", Input).value
        if not password or self.busy:
            return
        self.busy = True
        self._status("Unlocking... (key derivation takes a few seconds)")
        self.unlock(password, self.query_one("#unlock-code", Input).value)

    @work(exclusive=True, exit_on_error=False)
    async def unlock(self, password: str, code: str) -> None:
        try:
            values = await self.client.fetch_item(password, code)
        except TwoFactorRequired:
            code_input = self.query_one("#unlock-code", Input)
            code_input.display = True
            code_input.focus()
            self._status("Enter the code from your authenticator app, then Enter.")
            return
        except VaultError as exc:
            self._status(str(exc))
            self.query_one("#unlock-password", Input).focus()
            return
        except Exception as exc:  # noqa: BLE001
            self._status(f"{type(exc).__name__}: {exc}")
            return
        finally:
            self.busy = False
        self.dismiss(values)

    def _status(self, text: str) -> None:
        self.query_one("#unlock-status", Static).update(text)

    def action_skip(self) -> None:
        self.dismiss(None)
