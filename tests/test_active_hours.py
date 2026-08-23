"""
Tests for api/active_hours.py
Run with: pytest tests/test_active_hours.py -v

These tests need no FAISS index, no OPENAI_API_KEY, and no network.
"""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api.active_hours import (
    DHAKA,
    bot_is_active,
    is_within_active_hours,
    now_in_dhaka,
    validate_window,
)

# The production default window: overnight, wrapping midnight.
START, END = 23, 9


def dhaka(year, month, day, hour, minute=0, second=0):
    """Build a Dhaka-local aware datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=DHAKA)


# ============================================================
# Wrapping window (23 -> 9) — the production default
# ============================================================
@pytest.mark.parametrize(
    "dt, expected, description",
    [
        (dhaka(2026, 8, 23, 22, 59, 59), False, "last second before start"),
        (dhaka(2026, 8, 23, 23, 0, 0),   True,  "start boundary is INCLUSIVE"),
        (dhaka(2026, 8, 23, 23, 0, 1),   True,  "just inside, before midnight"),
        (dhaka(2026, 8, 24, 0, 0, 0),    True,  "midnight itself — the wrap point"),
        (dhaka(2026, 8, 24, 2, 0, 0),    True,  "inside, after midnight"),
        (dhaka(2026, 8, 24, 8, 59, 59),  True,  "last second inside"),
        (dhaka(2026, 8, 24, 9, 0, 0),    False, "end boundary is EXCLUSIVE"),
        (dhaka(2026, 8, 24, 9, 0, 1),    False, "just outside"),
        (dhaka(2026, 8, 24, 15, 0, 0),   False, "mid-afternoon, rep is working"),
    ],
)
def test_wrapping_window_boundaries(dt, expected, description):
    assert is_within_active_hours(dt, START, END) is expected, description


# ============================================================
# Non-wrapping window (9 -> 17) — proves the wrap branch didn't eat this case
# ============================================================
@pytest.mark.parametrize(
    "dt, expected, description",
    [
        (dhaka(2026, 8, 23, 8, 59, 59), False, "before start"),
        (dhaka(2026, 8, 23, 9, 0, 0),   True,  "start boundary inclusive"),
        (dhaka(2026, 8, 23, 12, 0, 0),  True,  "midday, inside"),
        (dhaka(2026, 8, 23, 16, 59, 59), True, "last second inside"),
        (dhaka(2026, 8, 23, 17, 0, 0),  False, "end boundary exclusive"),
        (dhaka(2026, 8, 23, 23, 30, 0), False, "late night, outside"),
        (dhaka(2026, 8, 23, 0, 30, 0),  False, "after midnight — must NOT wrap"),
    ],
)
def test_non_wrapping_window_boundaries(dt, expected, description):
    assert is_within_active_hours(dt, 9, 17) is expected, description


# ============================================================
# 0 -> 24 — the explicit always-active window
# ============================================================
@pytest.mark.parametrize(
    "dt, description",
    [
        (dhaka(2026, 8, 23, 0, 0, 0),   "midnight"),
        (dhaka(2026, 8, 23, 12, 0, 0),  "noon"),
        (dhaka(2026, 8, 23, 23, 59, 0), "23:59"),
    ],
)
def test_zero_to_twentyfour_is_always_active(dt, description):
    """0 -> 24 is how an operator writes 'never go inert'."""
    assert is_within_active_hours(dt, 0, 24) is True, description


def test_end_hour_24_is_accepted():
    validate_window(0, 24)
    validate_window(23, 24)


# ============================================================
# Degenerate and out-of-range windows must RAISE, never be guessed
# ============================================================
@pytest.mark.parametrize("hour", [0, 9, 12, 23])
def test_start_equal_end_raises(hour):
    """
    A zero-length window is ambiguous. Guessing 'always active' would let
    the bot talk over a rep mid-conversation — the exact failure this
    feature prevents — so it is rejected loudly instead.
    """
    with pytest.raises(ValueError) as exc:
        validate_window(hour, hour)
    assert "BOT_ACTIVE_START_HOUR=0" in str(exc.value)
    assert "BOT_ACTIVE_END_HOUR=24" in str(exc.value)


def test_start_equal_end_raises_through_the_predicate_too():
    """The override path validates as well — not just the config path."""
    with pytest.raises(ValueError):
        is_within_active_hours(dhaka(2026, 8, 23, 12), 9, 9)


@pytest.mark.parametrize("start", [-1, 24, 25, 99])
def test_start_hour_out_of_range_raises(start):
    """START tops out at 23 — it has no need for a 24 'end of day' value."""
    with pytest.raises(ValueError, match="BOT_ACTIVE_START_HOUR"):
        validate_window(start, 9)


@pytest.mark.parametrize("end", [-1, 25, 99])
def test_end_hour_out_of_range_raises(end):
    with pytest.raises(ValueError, match="BOT_ACTIVE_END_HOUR"):
        validate_window(23, end)


@pytest.mark.parametrize("bad", ["23", 23.0, None, True])
def test_non_integer_hours_raise(bad):
    """Wrong type is a TypeError; wrong value is a ValueError."""
    with pytest.raises(TypeError):
        validate_window(bad, 9)
    with pytest.raises(TypeError):
        validate_window(23, bad)


# ============================================================
# Timezone pinning — the requirement most likely to silently regress
# ============================================================
@pytest.fixture
def host_tz_new_york(monkeypatch):
    """Move the HOST timezone far from Dhaka (UTC-4/-5, and a day behind)."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    time.tzset()  # monkeypatch restored TZ; make libc agree again


def test_host_timezone_really_is_not_dhaka(host_tz_new_york):
    """
    Positive control for the test below. If this fails, the TZ fixture is
    not taking effect and the pinning test would pass vacuously.
    """
    naive_local_offset = datetime.now().astimezone().utcoffset()
    assert naive_local_offset != timedelta(hours=6)


def test_now_in_dhaka_ignores_host_timezone(host_tz_new_york):
    """Dhaka is UTC+6 year-round; the host being in New York must not matter."""
    assert now_in_dhaka().utcoffset() == timedelta(hours=6)


def test_bot_is_active_ignores_host_timezone(host_tz_new_york):
    """The convenience wrapper must not reintroduce host-local time."""
    assert bot_is_active() is is_within_active_hours(now_in_dhaka())


def test_aware_utc_instant_is_judged_by_its_dhaka_hour():
    """
    2026-08-23 20:00 UTC is 2026-08-24 02:00 in Dhaka — a different calendar
    day AND inside the window, while 20:00 alone would read as outside.
    Catches an implementation that compares a UTC hour to a Dhaka window.
    """
    utc_instant = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert utc_instant.astimezone(DHAKA).hour == 2
    assert is_within_active_hours(utc_instant, START, END) is True


def test_aware_utc_instant_outside_window():
    """05:00 UTC = 11:00 Dhaka — outside, though 05:00 alone reads as inside."""
    utc_instant = datetime(2026, 8, 23, 5, 0, tzinfo=timezone.utc)
    assert utc_instant.astimezone(DHAKA).hour == 11
    assert is_within_active_hours(utc_instant, START, END) is False


def test_naive_datetime_is_treated_as_dhaka_local():
    """Documented contract: a naive dt is assumed to already be Dhaka-local."""
    naive_inside = datetime(2026, 8, 24, 2, 0)  # noqa: DTZ001 — naive is the point
    naive_outside = datetime(2026, 8, 24, 12, 0)  # noqa: DTZ001
    assert is_within_active_hours(naive_inside, START, END) is True
    assert is_within_active_hours(naive_outside, START, END) is False


def test_dhaka_has_no_dst_across_the_year():
    """
    Asia/Dhaka is UTC+6 year-round (the 2009 DST experiment was not
    repeated), so the wrap logic never meets an ambiguous local hour.
    """
    offsets = {
        datetime(2026, m, 15, 12, tzinfo=ZoneInfo("Asia/Dhaka")).utcoffset()
        for m in range(1, 13)
    }
    assert offsets == {timedelta(hours=6)}


# ============================================================
# Defaults
# ============================================================
def test_configured_default_window_is_overnight():
    """
    The shipped default is deliberate: the bot covers the overnight hours
    when no rep is watching Page Inbox.
    """
    from config import BOT_ACTIVE_END_HOUR, BOT_ACTIVE_START_HOUR

    assert (BOT_ACTIVE_START_HOUR, BOT_ACTIVE_END_HOUR) == (23, 9)
