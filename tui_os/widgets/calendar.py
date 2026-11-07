"""Calendar tab: calcurse-style layout (appointments | month + todo)."""

from __future__ import annotations

import calendar as pycal
import datetime as dt

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from .. import mock_data

STYLE_BAR = "bold #FFFFFF on #0000FF"
STYLE_SELECTED = "bold #FFFFFF on #0000FF"
STYLE_TODAY = "bold #000000 on #00FF00"
STYLE_BUSY = "bold underline #FFFFFF"
STYLE_TIME = "bold #00FF00"


class CalendarPane(Vertical):
    BINDINGS = [
        Binding("h,left", "move(-1)", "◀ Day", show=False),
        Binding("l,right", "move(1)", "Day ▶", show=False),
        Binding("k,up", "move(-7)", "▲ Week", show=False),
        Binding("j,down", "move(7)", "Week ▼", show=False),
        Binding("H", "month(-1)", "◀ Month", key_display="H"),
        Binding("L", "month(1)", "Month ▶", key_display="L"),
        Binding("t", "today", "Today"),
    ]

    can_focus = True

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.today = dt.date.today()
        self.selected = self.today

    def compose(self) -> ComposeResult:
        with Horizontal(id="cal-main"):
            yield Static("", id="cal-appts", classes="cal-box")
            with Vertical(id="cal-side"):
                yield Static("", id="cal-month", classes="cal-box")
                yield Static("", id="cal-todo", classes="cal-box")
        yield Static("", id="cal-status", classes="bar")

    def on_mount(self) -> None:
        self.query_one("#cal-appts").border_title = "Appointments"
        self.query_one("#cal-month").border_title = "Calendar"
        self.query_one("#cal-todo").border_title = "TODO"
        self._render_all()

    # ----------------------------------------------------------------- render
    def _render_all(self) -> None:
        self._render_month()
        self._render_appointments()
        self._render_todo()
        self.query_one("#cal-status", Static).update(
            Text(
                f" {self.selected:%A %d %B %Y}   h/l:day  j/k:week  H/L:month  t:today ".ljust(200),
                STYLE_BAR,
            )
        )

    def _render_month(self) -> None:
        sel = self.selected
        text = Text(justify="center")
        text.append(f"{sel:%B %Y}\n\n", "bold #FFFFFF")
        text.append(" Mo Tu We Th Fr Sa Su\n", STYLE_TIME)
        for week in pycal.Calendar(firstweekday=0).monthdatescalendar(sel.year, sel.month):
            for day in week:
                cell = str(day.day)
                # Pad outside the styled span so underlines/highlights hug the digits.
                text.append(" " * (3 - len(cell)))
                if day.month != sel.month:
                    text.append(" " * len(cell))
                elif day == sel:
                    text.append(cell, STYLE_SELECTED)
                elif day == self.today:
                    text.append(cell, STYLE_TODAY)
                elif mock_data.appointments_for(day):
                    text.append(cell, STYLE_BUSY)
                else:
                    text.append(cell, "#FFFFFF")
            text.append(" \n")
        self.query_one("#cal-month", Static).update(text)

    def _render_appointments(self) -> None:
        appts = mock_data.appointments_for(self.selected)
        text = Text()
        if not appts:
            text.append("  (no appointments)", "#FFFFFF")
        for appt in appts:
            if appt.end is None:
                text.append("  * ", STYLE_TIME)
                text.append(f"{appt.start:%H:%M}       ", STYLE_TIME)
            else:
                text.append("  - ", STYLE_TIME)
                text.append(f"{appt.start:%H:%M} -> {appt.end:%H:%M}", STYLE_TIME)
            text.append(f"\n\t{appt.title}\n\n", "bold #FFFFFF")
        box = self.query_one("#cal-appts", Static)
        box.update(text)
        box.border_subtitle = f"{self.selected:%a %d %b}"

    def _render_todo(self) -> None:
        text = Text()
        for prio, task in mock_data.TODOS:
            text.append(f" {prio}. ", STYLE_TIME if prio <= 2 else "#FFFFFF")
            text.append(f"{task}\n", "#FFFFFF")
        text.rstrip()
        self.query_one("#cal-todo", Static).update(text)

    # ----------------------------------------------------------------- actions
    def action_move(self, days: int) -> None:
        self.selected += dt.timedelta(days=days)
        self._render_all()

    def action_month(self, delta: int) -> None:
        month_index = self.selected.month - 1 + delta
        year = self.selected.year + month_index // 12
        month = month_index % 12 + 1
        day = min(self.selected.day, pycal.monthrange(year, month)[1])
        self.selected = dt.date(year, month, day)
        self._render_all()

    def action_today(self) -> None:
        self.today = dt.date.today()
        self.selected = self.today
        self._render_all()
