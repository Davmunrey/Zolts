"""When a step is allowed to land.

`wait: 2d` lands wherever the arithmetic lands. Two days after a Friday
afternoon is Sunday afternoon; two days after that is 03:00 for a contact three
timezones away. Both are sends nobody reads.

The policy engine already denies on quiet hours, for voice, SMS and WhatsApp,
where the interruption is the harm. Email is different: nothing is harmed by
the message existing at 03:00, it is wasted. So this moves a send and never
cancels one — discarding a step because it fell on a Sunday would throw away
work over a scheduling detail.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from zolts.schedule import ScheduleError, Window, next_open, window_for

MADRID = ZoneInfo("Europe/Madrid")
BUSINESS = {"schedule": {"send_window": {
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "opens": "08:00", "closes": "18:00", "timezone": "Europe/Madrid"}}}


def _local(moment: datetime) -> str:
    return moment.astimezone(MADRID).strftime("%a %H:%M")


# -- declaring a window ---------------------------------------------------

def test_a_program_without_a_schedule_has_no_window():
    """Opt-in. Adding one silently to every program would move live sequences
    on a deploy."""
    assert window_for({}) is None
    assert window_for({"schedule": {}}) is None
    assert next_open(datetime(2026, 1, 17, 14, tzinfo=timezone.utc), None) == \
        datetime(2026, 1, 17, 14, tzinfo=timezone.utc)


def test_days_accept_names_or_numbers():
    assert window_for({"schedule": {"send_window": {"days": ["mon", "fri"]}}}).days == (0, 4)
    assert window_for({"schedule": {"send_window": {"days": [0, 6]}}}).days == (0, 6)


def test_a_window_that_wraps_midnight_is_refused():
    """It is two windows, and declaring it as one silently sends at the hour it
    was meant to avoid."""
    with pytest.raises(ScheduleError, match="wraps midnight"):
        window_for({"schedule": {"send_window": {"opens": "22:00", "closes": "06:00"}}})


def test_a_window_with_no_open_day_is_refused():
    with pytest.raises(ScheduleError, match="never sends"):
        window_for({"schedule": {"send_window": {"days": []}}})


def test_an_unknown_day_or_time_is_named():
    with pytest.raises(ScheduleError, match="is not a day"):
        window_for({"schedule": {"send_window": {"days": ["caturday"]}}})
    with pytest.raises(ScheduleError, match="not a time of day"):
        window_for({"schedule": {"send_window": {"opens": "half eight"}}})


def test_an_unknown_timezone_is_named():
    window = window_for({"schedule": {"send_window": {"timezone": "Mars/Olympus"}}})
    with pytest.raises(ScheduleError, match="unknown timezone"):
        _ = window.zone


# -- moving a send --------------------------------------------------------

def test_a_moment_already_inside_the_window_is_untouched():
    inside = datetime(2026, 1, 14, 10, 30, tzinfo=timezone.utc)   # Wed 11:30 Madrid
    assert next_open(inside, window_for(BUSINESS)) == inside


def test_before_the_window_opens_moves_to_the_opening():
    early = datetime(2026, 1, 14, 3, tzinfo=timezone.utc)          # Wed 04:00 Madrid
    assert _local(next_open(early, window_for(BUSINESS))) == "Wed 08:00"


def test_after_the_window_closes_moves_to_the_next_open_day():
    """This is the one an earlier version got wrong: the search walked forward
    to Monday and then returned the moment it started from."""
    friday_evening = datetime(2026, 1, 16, 20, tzinfo=timezone.utc)   # Fri 21:00
    assert _local(next_open(friday_evening, window_for(BUSINESS))) == "Mon 08:00"


def test_a_closed_day_moves_to_the_next_open_one():
    saturday = datetime(2026, 1, 17, 14, tzinfo=timezone.utc)
    sunday = datetime(2026, 1, 18, 9, tzinfo=timezone.utc)
    assert _local(next_open(saturday, window_for(BUSINESS))) == "Mon 08:00"
    assert _local(next_open(sunday, window_for(BUSINESS))) == "Mon 08:00"


def test_a_send_is_never_brought_forward():
    """Moving a step earlier would shorten a wait the program declared."""
    window = window_for(BUSINESS)
    for hour in range(0, 24):
        moment = datetime(2026, 1, 14, hour, tzinfo=timezone.utc)
        assert next_open(moment, window) >= moment


def test_the_window_is_the_customers_local_time_not_the_runtimes():
    """08:00 Madrid is 07:00 UTC in January and 06:00 UTC in July. A window
    stored in UTC drifts by an hour twice a year."""
    window = window_for(BUSINESS)
    january = next_open(datetime(2026, 1, 14, 3, tzinfo=timezone.utc), window)
    july = next_open(datetime(2026, 7, 15, 3, tzinfo=timezone.utc), window)
    assert january.hour == 7
    assert july.hour == 6
    assert _local(january) == _local(july) == "Wed 08:00"


def test_a_naive_datetime_is_refused():
    with pytest.raises(ScheduleError, match="aware datetime"):
        next_open(datetime(2026, 1, 14, 10), window_for(BUSINESS))


def test_a_window_open_every_day_still_moves_the_hour():
    always = Window(days=(0, 1, 2, 3, 4, 5, 6), opens=time(9), closes=time(17),
                    timezone="Europe/Madrid")
    assert _local(next_open(datetime(2026, 1, 17, 2, tzinfo=timezone.utc), always)) == \
        "Sat 09:00"
