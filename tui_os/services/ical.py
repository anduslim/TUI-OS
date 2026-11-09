"""Read-only calendar from an iCalendar (.ics) URL, e.g. Google's secret iCal address.

A small, dependency-free parser that covers what Google Calendar exports:
timed and all-day VEVENTs, TZID / UTC / floating times, RRULE (DAILY,
WEEKLY, MONTHLY, YEARLY with INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY,
BYMONTH), EXDATE, moved instances (RECURRENCE-ID) and cancelled events.
Recurring events are expanded once per refresh into a window around today,
so drawing the month view is a dictionary lookup.
"""

from __future__ import annotations

import calendar as pycal
import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from ..mock_data import Appointment

try:  # zoneinfo needs tz data; fall back to "treat as local" without it.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_MAX_INSTANCES = 5000


class CalendarError(Exception):
    pass


# ------------------------------------------------------------------ content lines
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """``NAME;P1=a;P2="b":value`` -> (NAME, {P1: a, P2: b}, value)."""
    # The value starts at the first ':' that is not inside a quoted param.
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return line.upper(), {}, ""
    name, *params = head.split(";")
    pdict: dict[str, str] = {}
    for p in params:
        k, _, v = p.partition("=")
        pdict[k.upper()] = v.strip('"')
    return name.upper(), pdict, value


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


# ------------------------------------------------------------------ date values
def _zone(tzid: str | None) -> dt.tzinfo | None:
    if not tzid or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _to_local(value: dt.datetime) -> dt.datetime:
    """Aware -> naive local wall time; naive values are already local."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def _parse_dt(value: str, params: dict[str, str]) -> dt.date | dt.datetime:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value[:8], "%Y%m%d").date()
    utc = value.endswith("Z")
    stamp = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return stamp.replace(tzinfo=dt.timezone.utc)
    tz = _zone(params.get("TZID"))
    return stamp.replace(tzinfo=tz) if tz else stamp


# ------------------------------------------------------------------ events
@dataclass
class Event:
    uid: str = ""
    summary: str = ""
    location: str = ""
    start: dt.date | dt.datetime | None = None
    end: dt.date | dt.datetime | None = None
    rrule: dict[str, str] = field(default_factory=dict)
    exdates: list[dt.date | dt.datetime] = field(default_factory=list)
    recurrence_id: dt.date | dt.datetime | None = None
    cancelled: bool = False

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, dt.datetime)


def parse_ics(text: str) -> list[Event]:
    events: list[Event] = []
    current: Event | None = None
    depth = 0  # nested components inside a VEVENT (VALARM) are skipped
    for line in _unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and current is None:
                current = Event()
            elif current is not None:
                depth += 1
            continue
        if name == "END":
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.upper() == "VEVENT":
                if current.start is not None:
                    events.append(current)
                current = None
            continue
        if current is None or depth:
            continue
        try:
            if name == "UID":
                current.uid = value
            elif name == "SUMMARY":
                current.summary = _unescape(value)
            elif name == "LOCATION":
                current.location = _unescape(value)
            elif name == "DTSTART":
                current.start = _parse_dt(value, params)
            elif name == "DTEND":
                current.end = _parse_dt(value, params)
            elif name == "DURATION" and current.start is not None:
                current.end = current.start + _parse_duration(value)
            elif name == "RRULE":
                current.rrule = dict(p.partition("=")[::2] for p in value.upper().split(";") if p)
            elif name == "EXDATE":
                current.exdates.extend(_parse_dt(v, params) for v in value.split(",") if v)
            elif name == "RECURRENCE-ID":
                current.recurrence_id = _parse_dt(value, params)
            elif name == "STATUS":
                current.cancelled = value.upper() == "CANCELLED"
        except ValueError:
            continue  # one malformed property must not drop the whole calendar
    return events


def _parse_duration(value: str) -> dt.timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        raise ValueError(value)
    sign, w, d, h, mi, s = m.groups()
    delta = dt.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0),
                         minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


# ------------------------------------------------------------------ recurrence
def _wall(value: dt.date | dt.datetime, tz: dt.tzinfo | None) -> dt.date | dt.datetime:
    """Express ``value`` as naive wall time in the event's zone (for comparisons)."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz).replace(tzinfo=None) if tz else _to_local(value)
        return value
    return value


def _nth_weekdays(year: int, month: int, spec: str) -> list[int]:
    """BYDAY entry like ``2TU``, ``-1FR`` or ``WE`` -> matching days of that month."""
    m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", spec)
    if not m:
        return []
    ordinal, wd = (int(m.group(1)) if m.group(1) else None), _WEEKDAYS[m.group(2)]
    days = [d for d in range(1, pycal.monthrange(year, month)[1] + 1)
            if dt.date(year, month, d).weekday() == wd]
    if ordinal is None:
        return days
    try:
        return [days[ordinal - 1 if ordinal > 0 else ordinal]]
    except IndexError:
        return []


def _candidates(start: dt.date, rule: dict[str, str]) -> "iter[dt.date]":
    """Dates generated by the rule, in order, from the period containing ``start``."""
    freq = rule.get("FREQ", "")
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    byday = [d for d in rule.get("BYDAY", "").split(",") if d]
    bymonthday = [int(d) for d in rule.get("BYMONTHDAY", "").split(",") if d]
    bymonth = [int(m) for m in rule.get("BYMONTH", "").split(",") if m]
    k = 0
    while True:
        if freq == "DAILY":
            yield start + dt.timedelta(days=k * interval)
        elif freq == "WEEKLY":
            week0 = start - dt.timedelta(days=start.weekday()) + dt.timedelta(weeks=k * interval)
            wds = sorted(_WEEKDAYS[d[-2:]] for d in byday if d[-2:] in _WEEKDAYS) or [start.weekday()]
            for wd in wds:
                yield week0 + dt.timedelta(days=wd)
        elif freq in ("MONTHLY", "YEARLY"):
            if freq == "MONTHLY":
                idx = start.month - 1 + k * interval
                months = [(start.year + idx // 12, idx % 12 + 1)]
            else:
                year = start.year + k * interval
                months = [(year, m) for m in (bymonth or [start.month])]
            for year, month in months:
                last = pycal.monthrange(year, month)[1]
                if byday:
                    days = sorted({d for spec in byday for d in _nth_weekdays(year, month, spec)})
                elif bymonthday:
                    days = sorted({d if d > 0 else last + d + 1 for d in bymonthday if 1 <= abs(d) <= last})
                else:
                    days = [start.day] if start.day <= last else []
                for d in days:
                    yield dt.date(year, month, d)
        else:
            return
        k += 1


def expand(events: list[Event], first: dt.date, last: dt.date) -> dict[dt.date, list[Appointment]]:
    """All instances that start between ``first`` and ``last`` (inclusive), by local date."""
    overrides: dict[str, list] = defaultdict(list)
    for ev in events:
        if ev.recurrence_id is not None:
            overrides[ev.uid].append(ev.recurrence_id)
    out: dict[dt.date, list[Appointment]] = defaultdict(list)
    for ev in events:
        if ev.cancelled or ev.start is None:
            continue
        duration = (ev.end - ev.start) if ev.end is not None else dt.timedelta(0)
        if not ev.rrule or ev.recurrence_id is not None:
            _emit(out, ev, ev.start, duration, first, last)
            continue
        tz = ev.start.tzinfo if isinstance(ev.start, dt.datetime) else None
        wall_start = _wall(ev.start, tz)
        start_date = wall_start if not isinstance(wall_start, dt.datetime) else wall_start.date()
        clock = wall_start.time() if isinstance(wall_start, dt.datetime) else None
        skip = {_key(_wall(x, tz)) for x in (*ev.exdates, *overrides.get(ev.uid, ()))}
        until = ev.rrule.get("UNTIL")
        until_v = _wall(_parse_dt(until, {}), tz) if until else None
        count = int(ev.rrule["COUNT"]) if ev.rrule.get("COUNT", "").isdigit() else None
        seen = 0
        for n, day in enumerate(_candidates(start_date, ev.rrule)):
            if n > _MAX_INSTANCES or day > last:
                break
            if day < start_date:
                continue
            inst = dt.datetime.combine(day, clock) if clock is not None else day
            if until_v is not None and _cmp_key(inst) > _cmp_key(until_v):
                break
            seen += 1
            if count is not None and seen > count:
                break
            if _key(inst) in skip:
                continue
            real = inst.replace(tzinfo=tz) if isinstance(inst, dt.datetime) and tz else inst
            _emit(out, ev, real, duration, first, last)
    for appts in out.values():
        appts.sort(key=lambda a: (not a.all_day, a.start))
    return dict(out)


def _key(value: dt.date | dt.datetime) -> str:
    if isinstance(value, dt.datetime):
        value = _to_local(value) if value.tzinfo else value
        return value.strftime("%Y%m%dT%H%M")
    return value.strftime("%Y%m%d")


def _cmp_key(value: dt.date | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.combine(value, dt.time.max)


def _emit(out, ev: Event, start, duration: dt.timedelta, first: dt.date, last: dt.date) -> None:
    title = ev.summary or "(busy)"
    if isinstance(start, dt.datetime):
        local = _to_local(start)
        if not first <= local.date() <= last:
            return
        end = _to_local(start + duration) if duration else None
        out[local.date()].append(Appointment(
            local.time(), end.time() if end and end.date() == local.date() else None,
            title, location=ev.location))
        return
    # All-day: DTEND is exclusive; a multi-day event appears on each day.
    days = max(1, duration.days)
    for i in range(days):
        day = start + dt.timedelta(days=i)
        if first <= day <= last:
            out[day].append(Appointment(dt.time(0, 0), None, title, location=ev.location, all_day=True))


# ------------------------------------------------------------------ fetching
class ICalCalendar:
    """Downloads the .ics feed and serves appointments per day."""

    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 past_days: int = 90, future_days: int = 400) -> None:
        # Google shows webcal:// links in some places; it is plain https.
        self.url = re.sub(r"^webcal://", "https://", url.strip(), flags=re.I)
        self._url_problem = ""
        if re.search(r"calendar\.google\.com/calendar/(embed|u/\d+/r|r)\b", self.url):
            self._url_problem = (
                "ical_url is Google's embed/web link. Use Settings > your calendar > "
                "Integrate calendar > \"Secret address in iCal format\" (ends in .ics).")
        self._transport = transport
        self.past_days = past_days
        self.future_days = future_days

    async def fetch(self, today: dt.date | None = None) -> dict[dt.date, list[Appointment]]:
        today = today or dt.date.today()
        if self._url_problem:
            raise CalendarError(self._url_problem)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0,
                                         follow_redirects=True) as client:
                resp = await client.get(self.url)
        except httpx.HTTPError as exc:
            raise CalendarError(f"calendar download failed: {exc}") from exc
        if resp.status_code != 200:
            raise CalendarError(f"calendar download failed: HTTP {resp.status_code}")
        if "BEGIN:VCALENDAR" not in resp.text[:2000]:
            raise CalendarError("the calendar URL did not return iCal data (check ical_url)")
        events = parse_ics(resp.text)
        return expand(events, today - dt.timedelta(days=self.past_days),
                      today + dt.timedelta(days=self.future_days))
