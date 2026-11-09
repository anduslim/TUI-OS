"""Mail tab: neomutt-style index + pager split.

With ``mail_address`` / ``mail_password`` set it reads the INBOX over IMAP and
sends over SMTP (Gmail by default); otherwise it shows the demo mailbox.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static, TextArea

from .. import mock_data
from ..mock_data import MailMessage
from ..services.mail import ImapMailbox, MailAccount, MailError, quote_reply

if TYPE_CHECKING:
    from ..config import Settings

STYLE_BAR = "bold #FFFFFF on #0000FF"
STYLE_HDR_KEY = "bold #00FF00"
STYLE_QUOTE = "#00FF00"


def mailbox_from_settings(settings: "Settings | None") -> ImapMailbox | None:
    if settings is None or not settings.mail_configured:
        return None
    return ImapMailbox(MailAccount(
        address=settings.mail_address, password=settings.mail_password, name=settings.mail_name,
        imap_host=settings.imap_host, imap_port=settings.imap_port,
        smtp_host=settings.smtp_host, smtp_port=settings.smtp_port,
    ))


class ComposeScreen(ModalScreen[tuple[str, str, str] | None]):
    """To / Subject / body editor. Ctrl+S sends, Esc cancels."""

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, to: str = "", subject: str = "", body: str = "") -> None:
        super().__init__()
        self._to, self._subject, self._body = to, subject, body

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Static(Text(" Compose  (Ctrl+S send, Esc cancel)".ljust(200), STYLE_BAR), classes="bar")
            yield Input(self._to, placeholder="To", id="compose-to")
            yield Input(self._subject, placeholder="Subject", id="compose-subject")
            yield TextArea(self._body, id="compose-body", soft_wrap=True, show_line_numbers=False)

    def on_mount(self) -> None:
        target = "#compose-body" if self._to else "#compose-to"
        widget = self.query_one(target)
        widget.focus()
        if isinstance(widget, TextArea):
            widget.move_cursor((0, 0))

    def action_send(self) -> None:
        to = self.query_one("#compose-to", Input).value.strip()
        if not to:
            self.app.notify("Add a recipient first.", severity="warning")
            self.query_one("#compose-to", Input).focus()
            return
        self.dismiss((to, self.query_one("#compose-subject", Input).value,
                      self.query_one("#compose-body", TextArea).text))

    def action_cancel(self) -> None:
        self.dismiss(None)


class MailPane(Vertical):
    BINDINGS = [
        Binding("j", "cursor(1)", "Next", show=False),
        Binding("k", "cursor(-1)", "Prev", show=False),
        Binding("d", "toggle_delete", "Del"),
        Binding("dollar_sign", "sync_deletes", "Sync", key_display="$"),
        Binding("f", "toggle_flag", "Flag"),
        Binding("N", "toggle_new", "New", key_display="N"),
        Binding("space", "pager_page(1)", "Page", show=False),
        Binding("minus", "pager_page(-1)", "Page up", show=False),
        Binding("r", "reply", "Reply"),
        Binding("m", "compose", "Mail"),
        Binding("g", "refresh", "Get"),
    ]

    def __init__(self, settings: "Settings | None" = None, *, mailbox: ImapMailbox | None = None,
                 id: str | None = None) -> None:
        super().__init__(id=id)
        self.settings = settings
        self.mailbox = mailbox or mailbox_from_settings(settings)
        self.messages: list[MailMessage] = [] if self.mailbox else mock_data.mailbox()
        self.deleted: set[int] = set()
        self._folder = "INBOX" if self.mailbox else "INBOX (demo)"
        self._state = ""

    @property
    def live(self) -> bool:
        return self.mailbox is not None

    def compose(self) -> ComposeResult:
        yield Static("", id="mail-status", classes="bar")
        table: DataTable = DataTable(id="mail-index", cursor_type="row", zebra_stripes=False)
        yield table
        yield Static("", id="mail-pager-bar", classes="bar")
        with VerticalScroll(id="mail-pager"):
            yield Static("", id="mail-body")
        yield Static(
            "d:Del  $:Sync  f:Flag  N:New  r:Reply  m:Mail  g:Get  j/k:Move  Space:Page",
            id="mail-help",
            classes="bar",
        )

    def on_mount(self) -> None:
        table = self.query_one("#mail-index", DataTable)
        table.add_column(" #", width=3, key="num")
        table.add_column("Flg", width=4, key="flags")
        table.add_column("Date", width=12, key="date")
        table.add_column("From", width=16, key="from")
        table.add_column("Subject", key="subject")
        self._fill_table()
        self._timer = None
        self._start_live()

    def _start_live(self) -> None:
        if not self.live:
            return
        self._state = "connecting..."
        self._update_status()
        self.refresh_mail()
        interval = self.settings.mail_refresh_interval if self.settings else 0
        if interval > 0 and self._timer is None:
            self._timer = self.set_interval(max(60.0, interval), self.refresh_mail)

    def reconfigure(self, settings: "Settings") -> None:
        """New account settings (e.g. just unlocked from the vault)."""
        self.settings = settings
        mailbox = mailbox_from_settings(settings)
        if mailbox is None:
            return
        self.mailbox = mailbox
        self.messages = []
        self.deleted.clear()
        self._folder = "INBOX"
        self._fill_table()
        self._start_live()

    # ----------------------------------------------------------------- render
    def _fill_table(self) -> None:
        table = self.query_one("#mail-index", DataTable)
        table.clear()
        for i, msg in enumerate(self.messages):
            table.add_row(*self._row(i, msg), key=str(i))
        self._update_status()
        if self.messages:
            self._show(0)
        else:
            self.query_one("#mail-body", Static).update("")

    def _row(self, i: int, msg: MailMessage) -> tuple[Text, ...]:
        flags = msg.flags
        if i in self.deleted:
            flags = "D" + flags[1:]
        style = "bold" if msg.new else ""
        # Text (not str) so subjects like "[tui-os] ..." aren't parsed as markup.
        return (
            Text(f"{i + 1:>2}"),
            Text(flags, "bold #00FF00" if flags.strip() else ""),
            Text(msg.date.strftime("%b %d %H:%M")),
            Text(msg.sender[:16], style),
            Text(msg.subject, style),
        )

    def _refresh_row(self, i: int) -> None:
        table = self.query_one("#mail-index", DataTable)
        for col, value in zip(("num", "flags", "date", "from", "subject"), self._row(i, self.messages[i])):
            table.update_cell(str(i), col, value)
        self._update_status()

    def _update_status(self) -> None:
        total = len(self.messages)
        new = sum(1 for m in self.messages if m.new)
        flagged = sum(1 for m in self.messages if m.flagged)
        status = (f"-*-NeoMutt: ={self._folder} [Msgs:{total} New:{new} Flag:{flagged} "
                  f"Del:{len(self.deleted)}]")
        if self._state:
            status += f" {self._state} "
        self.query_one("#mail-status", Static).update(Text(status.ljust(200, "-"), STYLE_BAR))

    def _show(self, i: int) -> None:
        msg = self.messages[i]
        body = Text()
        for key, value in (
            ("Date", msg.date.strftime("%a, %d %b %Y %H:%M")),
            ("From", f"{msg.sender} <{msg.sender_address}>" if msg.sender_address else msg.sender),
            ("To", msg.to),
            ("Subject", msg.subject),
        ):
            body.append(f"{key}: ", STYLE_HDR_KEY)
            body.append(f"{value}\n", "bold #FFFFFF")
        body.append("\n")
        for line in msg.body.split("\n"):
            body.append(line + "\n", STYLE_QUOTE if line.startswith(">") else "#FFFFFF")
        self.query_one("#mail-body", Static).update(body)
        self.query_one("#mail-pager", VerticalScroll).scroll_home(animate=False)
        self.query_one("#mail-pager-bar", Static).update(
            Text(f"-NeoMutt: {msg.subject} [{i + 1}/{len(self.messages)}]".ljust(200, "-"), STYLE_BAR)
        )

    # ----------------------------------------------------------------- server
    @work(exclusive=True, group="mail-fetch", exit_on_error=False)
    async def refresh_mail(self) -> None:
        assert self.mailbox is not None
        count = self.settings.mail_fetch_count if self.settings else 40
        try:
            messages = await asyncio.to_thread(self.mailbox.fetch, count)
        except MailError as exc:
            self._state = "offline"
            self._update_status()
            self.app.notify(str(exc), title="Mail", severity="error", timeout=10)
            return
        except Exception as exc:  # noqa: BLE001 - keep the tab alive
            self._state = "offline"
            self._update_status()
            self.app.notify(f"{type(exc).__name__}: {exc}", title="Mail", severity="error", timeout=10)
            return
        if messages == self.messages and not self.deleted:
            # Periodic refresh with nothing new: keep the table and the
            # selection as they are (no rebuild, no repaint).
            if self._state:
                self._state = ""
                self._update_status()
            return
        self.messages = messages
        self.deleted.clear()
        self._state = ""
        self._fill_table()

    def _server(self, fn, *args, done: str = "") -> None:
        """Run a blocking mailbox call off the UI thread; report failures."""
        if self.mailbox is None:
            return

        async def job() -> None:
            try:
                await asyncio.to_thread(fn, *args)
            except Exception as exc:  # noqa: BLE001
                self.app.notify(str(exc), title="Mail", severity="error", timeout=10)
                return
            if done:
                self.app.notify(done, title="Mail")

        self.run_worker(job(), group="mail-write", exit_on_error=False)

    # ----------------------------------------------------------------- events
    @on(DataTable.RowHighlighted, "#mail-index")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is None:
            return
        idx = int(event.row_key.value)
        if idx >= len(self.messages):
            return
        msg = self.messages[idx]
        if msg.new:
            msg.new = False
            self._refresh_row(idx)
            if msg.uid:
                self._server(self.mailbox.set_flag, msg.uid, "\\Seen", True)
        self._show(idx)

    @property
    def _current(self) -> int:
        return self.query_one("#mail-index", DataTable).cursor_row

    def action_cursor(self, delta: int) -> None:
        table = self.query_one("#mail-index", DataTable)
        table.move_cursor(row=max(0, min(len(self.messages) - 1, table.cursor_row + delta)), animate=False)

    def action_toggle_delete(self) -> None:
        if not self.messages:
            return
        i = self._current
        self.deleted.symmetric_difference_update({i})
        self._refresh_row(i)

    def action_sync_deletes(self) -> None:
        """neomutt's '$': apply the D marks (Gmail: archive out of the INBOX)."""
        if not self.deleted:
            self.app.notify("Nothing marked for deletion (d).", title="Mail")
            return
        doomed = sorted(self.deleted)
        if self.live:
            uids = [self.messages[i].uid for i in doomed if self.messages[i].uid]
            self._server(self.mailbox.expunge, uids, done=f"Removed {len(uids)} from INBOX.")
        self.messages = [m for i, m in enumerate(self.messages) if i not in self.deleted]
        self.deleted.clear()
        self._fill_table()

    def action_toggle_flag(self) -> None:
        if not self.messages:
            return
        msg = self.messages[self._current]
        msg.flagged = not msg.flagged
        self._refresh_row(self._current)
        if msg.uid:
            self._server(self.mailbox.set_flag, msg.uid, "\\Flagged", msg.flagged)

    def action_toggle_new(self) -> None:
        if not self.messages:
            return
        msg = self.messages[self._current]
        msg.new = not msg.new
        self._refresh_row(self._current)
        if msg.uid:
            self._server(self.mailbox.set_flag, msg.uid, "\\Seen", not msg.new)

    def action_pager_page(self, direction: int) -> None:
        pager = self.query_one("#mail-pager", VerticalScroll)
        if direction > 0:
            pager.scroll_page_down(animate=False)
        else:
            pager.scroll_page_up(animate=False)

    def action_refresh(self) -> None:
        if not self.live:
            self.app.notify("Demo mailbox: set mail_address and mail_password in config.toml.",
                            title="Mail")
            return
        self._state = "fetching..."
        self._update_status()
        self.refresh_mail()

    def action_reply(self) -> None:
        if not self.messages:
            return
        idx = self._current
        msg = self.messages[idx]
        subject = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
        to = msg.sender_address or msg.sender

        def sent(result: tuple[str, str, str] | None) -> None:
            if result is None:
                return
            if self._send(*result, in_reply_to=msg.message_id, references=msg.references):
                msg.replied = True
                self._refresh_row(idx)
                if msg.uid:
                    self._server(self.mailbox.set_flag, msg.uid, "\\Answered", True)

        self.app.push_screen(ComposeScreen(to, subject, quote_reply(msg)), sent)

    def action_compose(self) -> None:
        def sent(result: tuple[str, str, str] | None) -> None:
            if result is not None:
                self._send(*result)

        self.app.push_screen(ComposeScreen(), sent)

    def _send(self, to: str, subject: str, body: str, **headers: str) -> bool:
        if not self.live:
            self.app.notify("Demo mailbox: not sent. Set mail_address and mail_password in config.toml.",
                            title="Mail", severity="warning")
            return False
        mailbox = self.mailbox
        self._server(lambda: mailbox.send(to, subject, body, **headers), done=f"Sent to {to}.")
        return True
