"""
Tests for api/active_hours.py
Run with: pytest tests/test_active_hours.py -v

These tests need no FAISS index, no OPENAI_API_KEY, and no network.
"""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api import active_hours
from api.active_hours import (
    DHAKA,
    bot_is_active,
    describe_schedule,
    is_always_active_day,
    is_bot_active_at,
    is_within_active_hours,
    now_in_dhaka,
    validate_days,
    validate_window,
)

# The production default window: overnight, wrapping midnight.
START, END = 23, 9

# A fixed instant chosen so the two timezones DISAGREE about the window:
#   2026-08-23 20:00 UTC  ->  Dhaka    02:00  (INSIDE  23->9)
#                         ->  New York 16:00  (OUTSIDE 23->9)
# Freezing here is what makes the pinning assertions independent of when
# the suite happens to run — with the real clock they can agree by luck.
FROZEN_UTC = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)


class FrozenClock(datetime):
    """Drop-in for api.active_hours.datetime, pinned to FROZEN_UTC."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            # Mimic real datetime.now(): naive, in host-local time.
            return FROZEN_UTC.astimezone().replace(tzinfo=None)
        return FROZEN_UTC.astimezone(tz)


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


def test_bot_is_active_ignores_host_timezone(host_tz_new_york, monkeypatch):
    """
    The wrapper must read a Dhaka-pinned clock AND return the Dhaka answer.

    The previous version asserted
        bot_is_active() is is_within_active_hours(now_in_dhaka())
    which is bot_is_active's own definition — both sides moved together
    under any clock change, so it guarded nothing.

    Both halves of the property are asserted here because they fail to
    different mutations:
      - returning a NAIVE host-local time changes the answer (16:00 read
        as Dhaka-local is outside the window, 02:00 is inside)
      - returning an AWARE host-local time does NOT change the answer,
        because is_within_active_hours converts aware datetimes to Dhaka
        and so silently repairs it. Only the clock source itself shows it.
    """
    monkeypatch.setattr(active_hours, "datetime", FrozenClock)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_START_HOUR", START)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_END_HOUR", END)

    # Precondition: the frozen instant really does split the two zones, so
    # neither assertion below can hold by coincidence.
    assert FROZEN_UTC.astimezone(DHAKA).hour == 2, "Dhaka side: inside window"
    assert FROZEN_UTC.astimezone().hour == 16, "host side: outside window"

    assert bot_is_active() is True, "must return the Dhaka answer, not the host one"
    assert now_in_dhaka().utcoffset() == timedelta(hours=6), (
        "the wrapper's clock source must be Dhaka-pinned"
    )


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


# ============================================================
# Always-active days (BOT_ALWAYS_ACTIVE_DAYS)
# ============================================================
# Friday is the client's holiday: no rep is at Page Inbox all day, so the
# bot must cover Thu 23:00 -> Sat 09:00 as ONE continuous stretch.
#
# 2026-08-27/28/29 really are Thursday/Friday/Saturday — verified against
# the calendar, not assumed.
FRIDAY_ONLY = ("friday",)
THU, FRI, SAT = 27, 28, 29


def test_test_data_days_are_labelled_correctly():
    """
    Positive control for every THU/FRI/SAT test below: if these calendar
    dates were not the weekdays the names claim, the boundary table would
    be asserting the wrong thing and still pass.

    Asserted on weekday() indices rather than strftime("%A"), which is
    locale-dependent — the same trap WEEKDAY_NAMES exists to avoid.
    """
    assert dhaka(2026, 8, THU, 12).weekday() == 3
    assert dhaka(2026, 8, FRI, 12).weekday() == 4
    assert dhaka(2026, 8, SAT, 12).weekday() == 5


# ------------------------------------------------------------
# The three continuity boundaries
# ------------------------------------------------------------
@pytest.mark.parametrize(
    "dt, expected, description",
    [
        (dhaka(2026, 8, THU, 22, 59), False, "Thu 22:59 — before the stretch"),
        (dhaka(2026, 8, THU, 23, 0),  True,  "Thu 23:00 — window opens"),
        (dhaka(2026, 8, FRI, 8, 59),  True,  "Fri 08:59 — still the window"),
        (dhaka(2026, 8, FRI, 9, 0),   True,  "Fri 09:00 — window shut, DAY rule holds it"),
        (dhaka(2026, 8, FRI, 12, 0),  True,  "Fri noon — day rule alone"),
        (dhaka(2026, 8, FRI, 22, 59), True,  "Fri 22:59 — day rule alone"),
        (dhaka(2026, 8, FRI, 23, 0),  True,  "Fri 23:00 — both rules, no flicker"),
        (dhaka(2026, 8, SAT, 8, 59),  True,  "Sat 08:59 — day rule over, window carries it"),
        (dhaka(2026, 8, SAT, 9, 0),   False, "Sat 09:00 — the stretch ends"),
    ],
)
def test_friday_stretch_boundaries(dt, expected, description):
    """The three handover points are Fri 09:00, Fri 23:00 and Sat 09:00."""
    assert is_bot_active_at(dt, START, END, FRIDAY_ONLY) is expected, description


# ------------------------------------------------------------
# The sweep — the real continuity proof
# ------------------------------------------------------------
def _transitions(days, start_hour=START, end_hour=END):
    """
    Walk Thu 22:00 -> Sat 10:00 minute by minute (2160 samples) and return
    every point where the answer changes, as (datetime, new_value).
    """
    origin = dhaka(2026, 8, THU, 22, 0)
    previous = is_bot_active_at(origin, start_hour, end_hour, days)
    changes = []
    for step in range(1, 2160):
        moment = origin + timedelta(minutes=step)
        current = is_bot_active_at(moment, start_hour, end_hour, days)
        if current != previous:
            changes.append((moment, current))
            previous = current
    return changes


def test_friday_stretch_is_one_continuous_block():
    """
    Across the whole Thu -> Sat span the answer may change exactly twice:
    on at Thu 23:00, off at Sat 09:00. Nothing in between.

    The boundary table above only catches gaps at the three points I
    predicted. This catches ANY gap — an AND where the OR belongs (which
    fragments the block), an off-by-one on either edge — and names the
    minute it happened at.
    """
    assert _transitions(FRIDAY_ONLY) == [
        (dhaka(2026, 8, THU, 23, 0), True),
        (dhaka(2026, 8, SAT, 9, 0), False),
    ]


def test_the_sweep_can_still_see_a_gap():
    """
    Positive control for the sweep. With no always-active days the same
    span must show the ordinary nightly pattern — off at Fri 09:00, back on
    at Fri 23:00. If this ever matched the two-transition result above, the
    sweep would be measuring nothing.
    """
    assert _transitions(()) == [
        (dhaka(2026, 8, THU, 23, 0), True),
        (dhaka(2026, 8, FRI, 9, 0), False),
        (dhaka(2026, 8, FRI, 23, 0), True),
        (dhaka(2026, 8, SAT, 9, 0), False),
    ]


# ------------------------------------------------------------
# Only the configured day, and only when configured
# ------------------------------------------------------------
@pytest.mark.parametrize(
    "day, weekday_index, expected",
    [
        (24, 0, False),  # Monday
        (25, 1, False),  # Tuesday
        (26, 2, False),  # Wednesday
        (27, 3, False),  # Thursday
        (28, 4, True),   # Friday
        (29, 5, False),  # Saturday
        (30, 6, False),  # Sunday
    ],
)
def test_only_the_configured_weekday_is_always_active(day, weekday_index, expected):
    """
    Midday on each day of one week. Catches an off-by-one in the name ->
    index map, which the Thu/Fri/Sat tests cannot: a uniform +1 shift still
    produces a contiguous 34-hour block, just on the wrong days.
    """
    noon = dhaka(2026, 8, day, 12)
    assert noon.weekday() == weekday_index, "test data is mislabelled"
    assert is_always_active_day(noon, FRIDAY_ONLY) is expected


@pytest.mark.parametrize("day", [24, 25, 26, 27, 28, 29, 30])
@pytest.mark.parametrize("hour", [0, 8, 9, 12, 22, 23])
def test_empty_days_changes_nothing(day, hour):
    """
    The var is unset by default and must then be a no-op: across a full
    week, is_bot_active_at has to agree with the window predicate exactly.
    """
    moment = dhaka(2026, 8, day, hour)
    assert is_bot_active_at(moment, START, END, ()) is is_within_active_hours(
        moment, START, END
    )


# ------------------------------------------------------------
# Parsing and validation
# ------------------------------------------------------------
@pytest.mark.parametrize(
    "names, expected",
    [
        ((), frozenset()),
        (("friday",), frozenset({4})),
        ((" FRIDAY ",), frozenset({4})),
        (("Friday", "Saturday"), frozenset({4, 5})),
        (("friday", "friday"), frozenset({4})),
        (("monday", "sunday"), frozenset({0, 6})),
    ],
)
def test_validate_days_parses_names_case_insensitively(names, expected):
    assert validate_days(names) == expected


@pytest.mark.parametrize(
    "bad", ["fryday", "fri", "6", "friday;saturday", "friday,saturday", ""]
)
def test_unknown_day_name_raises(bad):
    """
    A typo must stop the container at boot. Read as "no always-active days"
    it would look exactly like the feature being switched off — silence all
    Friday with nothing in the logs to explain it.

    "fri" is in here deliberately: full names only, so that there is exactly
    one spelling per day to parse and to get wrong.
    """
    with pytest.raises(ValueError) as exc:
        validate_days((bad,))
    assert "friday" in str(exc.value), "the error must list the valid names"


@pytest.mark.parametrize("bad", [4, None, True, 4.0])
def test_non_string_day_raises(bad):
    with pytest.raises(TypeError):
        validate_days((bad,))


def test_bare_string_is_rejected():
    """
    validate_days("friday") would otherwise iterate characters and reject
    'f' — a real error carrying a useless message.
    """
    with pytest.raises(TypeError, match="sequence"):
        validate_days("friday")


def test_bad_day_raises_through_the_predicates_too():
    """The override path validates as well, matching the window's behaviour."""
    with pytest.raises(ValueError):
        is_always_active_day(dhaka(2026, 8, FRI, 12), ("fryday",))
    with pytest.raises(ValueError):
        is_bot_active_at(dhaka(2026, 8, FRI, 12), START, END, ("fryday",))


def test_configured_days_are_validated_at_import():
    """
    The configured value is parsed and validated at import, like the window,
    so a typo stops the container from booting.
    """
    assert isinstance(active_hours.ALWAYS_ACTIVE_WEEKDAYS, frozenset)
    assert all(
        isinstance(d, int) and 0 <= d <= 6
        for d in active_hours.ALWAYS_ACTIVE_WEEKDAYS
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ()),
        ("friday", ("friday",)),
        ("  Friday  ", ("friday",)),
        ("friday,saturday", ("friday", "saturday")),
        ("friday, SATURDAY ,", ("friday", "saturday")),
        (",,", ()),
    ],
)
def test_day_list_from_env_splits_and_normalises(raw, expected, monkeypatch):
    """Splitting and normalisation only — legality is validate_days' job."""
    from config import _day_list_from_env

    monkeypatch.setenv("BOT_ALWAYS_ACTIVE_DAYS_TEST", raw)
    assert _day_list_from_env("BOT_ALWAYS_ACTIVE_DAYS_TEST") == expected


def test_day_list_from_env_defaults_to_empty(monkeypatch):
    """Unset means unchanged behaviour — the whole opt-in guarantee."""
    from config import _day_list_from_env

    monkeypatch.delenv("BOT_ALWAYS_ACTIVE_DAYS_TEST", raising=False)
    assert _day_list_from_env("BOT_ALWAYS_ACTIVE_DAYS_TEST") == ()


# ============================================================
# Timezone pinning for the WEEKDAY — the discriminating tests
# ============================================================
# UTC and Dhaka disagree about the weekday during exactly Dhaka 00:00-05:59
# (UTC+6). That region sits ENTIRELY INSIDE the 23 -> 9 window, so
# is_within_active_hours already returns True for every instant where a
# naive dt.weekday() would be wrong, and the OR swallows the bug. A test of
# the shipped configuration therefore passes identically whether the
# weekday is read before or after conversion. It proves nothing.
#
# Hence two shapes:
#   (a) assert on the day predicate ALONE, which has no OR to hide behind
#   (b) assert on the combined predicate under a 9 -> 17 window, where
#       Dhaka 01:00 is OUTSIDE the window so the day rule alone decides
#
# DO NOT "tidy" (b) to use START/END. The production values are precisely
# the ones that mask this bug; that substitution silently guts the test.

# Thursday in UTC, Friday 01:00 in Dhaka.
UTC_THU_EVENING = datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc)
# Friday in UTC, Saturday 01:00 in Dhaka.
UTC_FRI_EVENING = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("instant", [UTC_THU_EVENING, UTC_FRI_EVENING])
def test_utc_and_dhaka_really_disagree_about_the_weekday(instant):
    """
    Positive control for (a) and (b) below. If UTC and Dhaka ever agreed on
    these instants those tests would be vacuous — this one goes red first.
    """
    assert instant.weekday() != instant.astimezone(DHAKA).weekday()


@pytest.mark.parametrize(
    "instant, expected, description",
    [
        (UTC_THU_EVENING, True, "Dhaka Friday 01:00 — a naive read says Thursday"),
        (UTC_FRI_EVENING, False, "Dhaka Saturday 01:00 — a naive read says Friday"),
    ],
)
def test_weekday_is_read_in_dhaka_not_utc(instant, expected, description):
    """
    (a) The day predicate alone. Both directions are asserted because a
    naive implementation gets one wrong each way: it misses the start of
    Friday (bot silent until 06:00 Friday) AND overruns into Saturday (bot
    still talking until 06:00 Saturday). The second is the half a
    one-directional test would wave through.
    """
    assert is_always_active_day(instant, FRIDAY_ONLY) is expected, description


@pytest.mark.parametrize(
    "instant, expected",
    [(UTC_THU_EVENING, True), (UTC_FRI_EVENING, False)],
)
def test_combined_predicate_reads_the_weekday_in_dhaka(instant, expected):
    """
    (b) The same instants through is_bot_active_at, under a 9 -> 17 window
    rather than the production 23 -> 9. See the block comment above: with
    23 -> 9 the window returns True for both instants and the OR hides the
    day rule's answer, so this test would pass under a naive weekday.
    """
    assert is_within_active_hours(instant, 9, 17) is False, (
        "the window must NOT cover these instants, or the OR masks the day rule"
    )
    assert is_bot_active_at(instant, 9, 17, FRIDAY_ONLY) is expected


def test_bot_is_active_uses_the_dhaka_weekday(host_tz_new_york, monkeypatch):
    """
    The wrapper end to end, with the clock source as the thing under test.

    FROZEN_UTC is 2026-08-23 20:00 UTC — Dhaka Monday 02:00, New York
    Sunday 16:00 — so host and Dhaka disagree about the DAY, not just the
    hour. The window is set to 10 -> 14, which covers NEITHER side, so only
    the weekday rule can decide the answer.

    A clock source returning naive host-local time therefore reads Sunday
    and returns False. Note this does not catch a naive weekday READ:
    now_in_dhaka() hands over an already-Dhaka datetime, so the conversion
    is a no-op on this path. That mutation is caught by (a) and (b) above —
    the two tests are not redundant.
    """
    monkeypatch.setattr(active_hours, "datetime", FrozenClock)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_START_HOUR", 10)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_END_HOUR", 14)
    monkeypatch.setattr(active_hours, "ALWAYS_ACTIVE_WEEKDAYS", frozenset({0}))

    # Preconditions: the frozen instant splits the two zones by weekday, and
    # the window covers neither side, so nothing here can hold by luck.
    assert FROZEN_UTC.astimezone(DHAKA).weekday() == 0, "Dhaka side: Monday 02:00"
    assert FROZEN_UTC.astimezone().weekday() == 6, "host side: Sunday 16:00"
    assert is_within_active_hours(FROZEN_UTC, 10, 14) is False
    assert is_within_active_hours(FROZEN_UTC.astimezone().replace(tzinfo=None), 10, 14) is False

    assert bot_is_active() is True, "must use the Dhaka weekday, not the host one"
    assert now_in_dhaka().utcoffset() == timedelta(hours=6), (
        "the wrapper's clock source must be Dhaka-pinned"
    )


# ============================================================
# The gate's log line
# ============================================================
def test_describe_schedule_is_just_the_window_when_no_days(monkeypatch):
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_START_HOUR", 23)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_END_HOUR", 9)
    monkeypatch.setattr(active_hours, "ALWAYS_ACTIVE_WEEKDAYS", frozenset())
    assert describe_schedule() == "23:00-09:00 Asia/Dhaka"


def test_describe_schedule_names_the_configured_days(monkeypatch):
    """
    This string is what someone reads when asking "why was the bot silent
    on Friday", so it has to state the whole rule set, not just the window.
    """
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_START_HOUR", 23)
    monkeypatch.setattr(active_hours, "BOT_ACTIVE_END_HOUR", 9)
    monkeypatch.setattr(active_hours, "ALWAYS_ACTIVE_WEEKDAYS", frozenset({4, 5}))
    assert describe_schedule() == (
        "23:00-09:00 Asia/Dhaka, plus all day: friday, saturday"
    )
