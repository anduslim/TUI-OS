"""Calendar tab: calcurse-style layout (appointments | month + todo).

With ``ical_url`` set it shows that read-only iCal feed (Google Calendar's
secret address) and lists the coming week where the demo TODO list was.
"""

from __future__ import annotations

import calendar as pycal
import datetime as dt

from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from .. import mock_data
from ..mock_data import Appointment
from ..services.ical import ICalCalendar

if TYPE_CHECKING:
    from ..config import Settings

STYLE_BAR = "bold #FFFFFF on #0000FF"
STYLE_SELECTED = "bold #FFFFFF on #0000FF"  # selected: solid blue block
STYLE_TODAY = "bold #00FF00"  # today: green numerals
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
        Binding("g", "refresh", "Get"),
    ]

    can_focus = True

    def __init__(self, settings: "Settings | None" = None, *, source: ICalCalendar | None = None,
                 id: str | None = None) -> None:
        super().__init__(id=id)
        self.settings = settings
        if source is None and settings is not None and settings.ical_url:
            source = ICalCalendar(settings.ical_url)
        self.source = source
        self.today = dt.date.today()
        self.selected = self.today
        self._by_day: dict[dt.date, list[Appointment]] = {}
        self._state = "loading..." if source else ""

    @property
    def live(self) -> bool:
        return self.source is not None

    def appointments_for(self, day: dt.date) -> list[Appointment]:
        if self.live:
            return self._by_day.get(day, [])
        return mock_data.appointments_for(day)

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
        self.query_one("#cal-todo").border_title = "Next 7 days" if self.live else "TODO"
        self._render_all()
        self._timer = None
        self._start_live()

    def _start_live(self) -> None:
        if not self.live:
            return
        self.query_one("#cal-todo").border_title = "Next 7 days"
        self.refresh_calendar()
        interval = self.settings.calendar_refresh_interval if self.settings else 0
        if interval > 0 and self._timer is None:
            self._timer = self.set_interval(max(60.0, interval), self.refresh_calendar)

    def reconfigure(self, settings: "Settings") -> None:
        """New settings (e.g. ical_url just unlocked from the vault)."""
        self.settings = settings
        if not settings.ical_url:
            return
        self.source = ICalCalendar(settings.ical_url)
        self._by_day = {}
        self._state = "loading..."
        self._render_all()
        self._start_live()

    @work(exclusive=True, group="calendar-fetch", exit_on_error=False)
    async def refresh_calendar(self) -> None:
        assert self.source is not None
        try:
            self._by_day = await self.source.fetch(dt.date.today())
        except Exception as exc:  # noqa: BLE001 - keep the tab alive, show why
            self._state = "offline"
            self._render_all()
            self.app.notify(str(exc), title="Calendar", severity="error", timeout=10)
            return
        self._state = ""
        self._render_all()

    # ----------------------------------------------------------------- render
    def _render_all(self) -> None:
        self._render_month()
        self._render_appointments()
        self._render_todo()
        self.query_one("#cal-status", Static).update(
            Text(
                (f" {self.selected:%A %d %B %Y}   h/l:day  j/k:week  H/L:month  t:today  g:get"
                 + (f"   [{self._state}]" if self._state else "")).ljust(200),
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
                elif self.appointments_for(day):
                    text.append(cell, STYLE_BUSY)
                else:
                    text.append(cell, "#FFFFFF")
            text.append(" \n")
        self.query_one("#cal-month", Static).update(text)

    def _render_appointments(self) -> None:
        appts = self.appointments_for(self.selected)
        text = Text()
        if not appts:
            text.append("  (no appointments)", "#FFFFFF")
        for appt in appts:
            if appt.all_day:
                text.append("  * all day      ", STYLE_TIME)
            elif appt.end is None:
                text.append("  * ", STYLE_TIME)
                text.append(f"{appt.start:%H:%M}       ", STYLE_TIME)
            else:
                text.append("  - ", STYLE_TIME)
                text.append(f"{appt.start:%H:%M} -> {appt.end:%H:%M}", STYLE_TIME)
            text.append(f"\n\t{appt.title}\n", "bold #FFFFFF")
            if appt.location:
                text.append(f"\t@ {appt.location}\n", "#FFFFFF")
            text.append("\n")
        box = self.query_one("#cal-appts", Static)
        box.update(text)
        box.border_subtitle = f"{self.selected:%a %d %b}"

    def _render_todo(self) -> None:
        text = Text()
        if self.live:
            for offset in range(1, 8):
                day = self.today + dt.timedelta(days=offset)
                for appt in self.appointments_for(day):
                    text.append(f" {day:%a %d} ", STYLE_TIME)
                    text.append("all day " if appt.all_day else f"{appt.start:%H:%M}   ", STYLE_TIME)
                    text.append(f"{appt.title}\n", "#FFFFFF")
            if not text.plain:
                text.append(" (nothing scheduled)", "#FFFFFF")
            text.rstrip()
            self.query_one("#cal-todo", Static).update(text)
            return
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

    def action_refresh(self) -> None:
        if not self.live:
            self.app.notify("Demo calendar: set ical_url in config.toml.", title="Calendar")
            return
        self._state = "loading..."
        self._render_all()
        self.refresh_calendar()

    def action_today(self) -> None:
        self.today = dt.date.today()
        self.selected = self.today
        self._render_all()
