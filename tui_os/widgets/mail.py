"""Mail tab: neomutt-style index + pager split (mocked mailbox)."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Static

from .. import mock_data
from ..mock_data import MailMessage

STYLE_BAR = "bold #FFFFFF on #0000FF"
STYLE_HDR_KEY = "bold #00FF00"
STYLE_QUOTE = "#00FF00"


class MailPane(Vertical):
    BINDINGS = [
        Binding("j", "cursor(1)", "Next", show=False),
        Binding("k", "cursor(-1)", "Prev", show=False),
        Binding("d", "toggle_delete", "Del"),
        Binding("f", "toggle_flag", "Flag"),
        Binding("N", "toggle_new", "New", key_display="N"),
        Binding("space", "pager_page(1)", "Page", show=False),
        Binding("minus", "pager_page(-1)", "Page up", show=False),
        Binding("r", "reply", "Reply"),
        Binding("m", "compose", "Mail"),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.messages: list[MailMessage] = mock_data.mailbox()
        self.deleted: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Static("", id="mail-status", classes="bar")
        table: DataTable = DataTable(id="mail-index", cursor_type="row", zebra_stripes=False)
        yield table
        yield Static("", id="mail-pager-bar", classes="bar")
        with VerticalScroll(id="mail-pager"):
            yield Static("", id="mail-body")
        yield Static(
            "^q:Quit  d:Del  f:Flag  N:New  r:Reply  m:Mail  j/k:Move  Space:Page",
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
        for i, msg in enumerate(self.messages):
            table.add_row(*self._row(i, msg), key=str(i))
        self._update_status()
        if self.messages:
            self._show(0)

    # ----------------------------------------------------------------- render
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
        status = f"-*-NeoMutt: =INBOX [Msgs:{total} New:{new} Flag:{flagged} Del:{len(self.deleted)}]"
        self.query_one("#mail-status", Static).update(Text(status.ljust(200, "-"), STYLE_BAR))

    def _show(self, i: int) -> None:
        msg = self.messages[i]
        body = Text()
        for key, value in (
            ("Date", msg.date.strftime("%a, %d %b %Y %H:%M")),
            ("From", msg.sender),
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

    # ----------------------------------------------------------------- events
    @on(DataTable.RowHighlighted, "#mail-index")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is not None:
            idx = int(event.row_key.value)
            if self.messages[idx].new:
                self.messages[idx].new = False
                self._refresh_row(idx)
            self._show(idx)

    @property
    def _current(self) -> int:
        return self.query_one("#mail-index", DataTable).cursor_row

    def action_cursor(self, delta: int) -> None:
        table = self.query_one("#mail-index", DataTable)
        table.move_cursor(row=max(0, min(len(self.messages) - 1, table.cursor_row + delta)), animate=False)

    def action_toggle_delete(self) -> None:
        i = self._current
        self.deleted.symmetric_difference_update({i})
        self._refresh_row(i)

    def action_toggle_flag(self) -> None:
        msg = self.messages[self._current]
        msg.flagged = not msg.flagged
        self._refresh_row(self._current)

    def action_toggle_new(self) -> None:
        msg = self.messages[self._current]
        msg.new = not msg.new
        self._refresh_row(self._current)

    def action_pager_page(self, direction: int) -> None:
        pager = self.query_one("#mail-pager", VerticalScroll)
        if direction > 0:
            pager.scroll_page_down(animate=False)
        else:
            pager.scroll_page_up(animate=False)

    def action_reply(self) -> None:
        msg = self.messages[self._current]
        self.app.notify(f"Reply to {msg.sender}: compose is not wired to SMTP yet (mock).")

    def action_compose(self) -> None:
        self.app.notify("Compose: mock mailbox — hook an SMTP/IMAP provider in mock_data.")
