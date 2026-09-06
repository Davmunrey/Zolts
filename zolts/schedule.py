"""When a step is allowed to land.

`wait: 2d` lands wherever the arithmetic lands. Two days after a Friday
afternoon enrollment is Sunday afternoon; two days after that is Tuesday at
03:00 for a contact in another timezone. Both are sends nobody reads, and one
of them is a send at an hour a regulator has an opinion about.

The policy engine already denies on quiet hours — for voice, SMS and WhatsApp,
where an interruption at 03:00 is the harm itself. Email is different: nothing
is harmed by the message existing at 03:00, it is simply wasted. So this is not
a second policy check. **It moves the send; it never cancels it.** Denying a
step because it happened to fall on a Sunday would discard work over a
scheduling detail.

Pure arithmetic on aware datetimes, like everything else in `zolts/`. The
tenant's timezone and the program's declared window are inputs; nothing here
reads a clock it was not given.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Monday is 0, matching `datetime.weekday()`. Weekdays, because a business
# sequence that lands on Saturday is a sequence that lands in Monday's unread
# pile with two days of decay on it.
DEFAULT_DAYS = (0, 1, 2, 3, 4)
DEFAULT_WINDOW = (time(8, 0), time(18, 0))

_DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    """When this program may land a step, in the tenant's own timezone."""
    days: tuple[int, ...] = DEFAULT_DAYS
    opens: time = DEFAULT_WINDOW[0]
    closes: time = DEFAULT_WINDOW[1]
    timezone: str = "UTC"

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ScheduleError(f"unknown timezone '{self.timezone}'") from exc


def _parse_time(value: object, fallback: time) -> time:
    if value is None:
        return fallback
    text = str(value).strip()
    try:
        hour, _, minute = text.partition(":")
        return time(int(hour), int(minute or 0))
    except ValueError as exc:
        raise ScheduleError(f"'{text}' is not a time of day, expected HH:MM") from exc


def window_for(spec: dict, timezone: str = "UTC") -> Window | None:
    """The program's declared send window, or nothing when it declares none.

    Nothing means "land whenever the wait says", which is the behaviour every
    program had before this existed. A window is opt-in: adding one silently to
    every program would move live sequences on a deploy.
    """
    declared = (spec.get("schedule") or {}).get("send_window")
    if not declared:
        return None

    days = declared.get("days")
    if days is None:
        parsed_days = DEFAULT_DAYS
    else:
        parsed_days = tuple(_day(d) for d in days)
        if not parsed_days:
            raise ScheduleError("send_window.days is empty; a window with no open "
                                "day never sends")
    opens = _parse_time(declared.get("opens"), DEFAULT_WINDOW[0])
    closes = _parse_time(declared.get("closes"), DEFAULT_WINDOW[1])
    if opens >= closes:
        raise ScheduleError(
            f"send_window opens at {opens} and closes at {closes}; a window that "
            "wraps midnight is two windows, and declaring it as one silently "
            "sends at the hour it was meant to avoid")
    return Window(days=parsed_days, opens=opens, closes=closes,
                  timezone=declared.get("timezone") or timezone)


def _day(value: object) -> int:
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ScheduleError(f"day {value} is out of range; Monday is 0 and Sunday is 6")
    key = str(value).strip().lower()[:3]
    if key not in _DAY_NAMES:
        raise ScheduleError(f"'{value}' is not a day; use mon-sun or 0-6")
    return _DAY_NAMES[key]


def next_open(moment: datetime, window: Window | None) -> datetime:
    """The first instant at or after `moment` that the window admits.

    Returns `moment` unchanged when there is no window, and when the moment is
    already inside one. A step is never moved earlier: bringing a send forward
    into today's window would shorten a wait the program declared.
    """
    if window is None:
        return moment
    if moment.tzinfo is None:
        raise ScheduleError("next_open needs an aware datetime; a naive one has "
                            "no timezone to convert from")

    local = moment.astimezone(window.zone)
    moved = False
    for _ in range(8):                       # a full week, plus the day itself
        if local.weekday() in window.days:
            if local.time() < window.opens:
                local = local.replace(hour=window.opens.hour,
                                      minute=window.opens.minute,
                                      second=0, microsecond=0)
                return local.astimezone(moment.tzinfo)
            if local.time() < window.closes:
                # Inside a window. On the first pass that is the original
                # moment, exact to the microsecond. After the loop has moved a
                # day it is the advanced one — returning `moment` here made a
                # Saturday afternoon stay on Saturday, because the search
                # walked to Monday and then handed back where it started.
                return moment if not moved else local.astimezone(moment.tzinfo)
        # Past today's close, or a closed day: try the next day at open.
        local = (local + timedelta(days=1)).replace(
            hour=window.opens.hour, minute=window.opens.minute,
            second=0, microsecond=0)
        moved = True
    # Unreachable while `days` is non-empty, which `window_for` enforces.
    raise ScheduleError("no open day found within a week of the requested moment")
