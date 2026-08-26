"""
Active-hours gate — decides whether the bot is allowed to act at all.

The bot serves the overnight shift: outside a configured window it must be
completely inert (no Send API calls, no pause_state reads or writes, no RAG).
See api/messenger.py for where the gate is applied.

Two things live here, deliberately split:

  - is_within_active_hours(dt, ...) is PURE. It takes the datetime explicitly
    so tests never have to monkeypatch a clock.
  - now_in_dhaka() is the single place the real clock is read, so integration
    tests have exactly one symbol to patch.

The timezone is pinned to Asia/Dhaka and is NOT configurable. The host or
container TZ is irrelevant — a server in UTC and a laptop in New York both
compute the same window.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    BOT_ACTIVE_END_HOUR,
    BOT_ACTIVE_START_HOUR,
    BOT_ALWAYS_ACTIVE_DAYS,
    BOT_TIMEZONE,
)

log = logging.getLogger(__name__)

# Resolved once at import — ZoneInfo instances are immutable and cacheable.
DHAKA = ZoneInfo(BOT_TIMEZONE)

# END_HOUR accepts 24 as "end of day" so that 0 -> 24 is an explicit,
# writable always-active window. START_HOUR has no such need: 0 already
# means midnight.
MIN_HOUR = 0
MAX_START_HOUR = 23
MAX_END_HOUR = 24

# Index == datetime.weekday() (Monday == 0). Hardcoded on purpose: both
# calendar.day_name and strftime("%A") are locale-dependent, so a container
# with a non-English LC_TIME would fail to match "friday" and read a valid
# config as "no always-active days". Do not "simplify" this to either.
WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


# ============================================================
# Window validation
# ============================================================
def validate_window(start_hour: int, end_hour: int) -> None:
    """
    Raise unless (start_hour, end_hour) describes a usable window.

    TypeError for a non-integer hour, ValueError for an out-of-range or
    degenerate one.

    Called at import time on the configured values, and again by
    is_within_active_hours() on any explicitly-passed override, so a bad
    window can never be silently interpreted.

    A degenerate start == end window is rejected rather than defaulted.
    Guessing "always active" from a typo would let the bot talk over a rep
    mid-conversation — the exact failure this feature exists to prevent —
    and guessing "never active" would silently kill the bot with no error
    anywhere. A loud failure at boot beats both.
    """
    if not isinstance(start_hour, int) or isinstance(start_hour, bool):
        raise TypeError(f"BOT_ACTIVE_START_HOUR must be an int, got {start_hour!r}")
    if not isinstance(end_hour, int) or isinstance(end_hour, bool):
        raise TypeError(f"BOT_ACTIVE_END_HOUR must be an int, got {end_hour!r}")

    if not MIN_HOUR <= start_hour <= MAX_START_HOUR:
        raise ValueError(
            f"BOT_ACTIVE_START_HOUR must be {MIN_HOUR}-{MAX_START_HOUR}, "
            f"got {start_hour}"
        )
    if not MIN_HOUR <= end_hour <= MAX_END_HOUR:
        raise ValueError(
            f"BOT_ACTIVE_END_HOUR must be {MIN_HOUR}-{MAX_END_HOUR} "
            f"(24 = end of day), got {end_hour}"
        )

    if start_hour == end_hour:
        raise ValueError(
            f"BOT_ACTIVE_START_HOUR and BOT_ACTIVE_END_HOUR are both "
            f"{start_hour} — a zero-length window is ambiguous and is "
            f"rejected rather than guessed. For an always-active bot set "
            f"BOT_ACTIVE_START_HOUR=0 and BOT_ACTIVE_END_HOUR=24."
        )


# Fail at import, not at the first webhook. A misconfigured window should
# stop the container from coming up, not surface at 3am as silence.
validate_window(BOT_ACTIVE_START_HOUR, BOT_ACTIVE_END_HOUR)


# ============================================================
# Always-active day validation
# ============================================================
def validate_days(names) -> frozenset[int]:
    """
    Parse weekday names into datetime.weekday() indices, raising on anything
    unusable.

    TypeError for a non-string entry (or for a bare string, which would
    otherwise iterate characters and complain about 'f'), ValueError for a
    name that is not one of the seven English weekday names. Matching is
    case-insensitive and surrounding whitespace is ignored, so " FRIDAY "
    is accepted.

    Called at import time on the configured value, and again by the
    predicates on any explicitly-passed override, so a typo can never be
    read as "no always-active days" — which looks exactly like the feature
    being switched off: silence all Friday with nothing in the logs.
    """
    if isinstance(names, str):
        raise TypeError(
            f"BOT_ALWAYS_ACTIVE_DAYS must be a sequence of day names, not a "
            f"single string ({names!r}) — pass e.g. ('friday',)."
        )

    days = set()
    for name in names:
        if not isinstance(name, str):
            raise TypeError(
                f"BOT_ALWAYS_ACTIVE_DAYS entries must be strings, got {name!r}"
            )
        key = name.strip().lower()
        if key not in WEEKDAY_NAMES:
            raise ValueError(
                f"BOT_ALWAYS_ACTIVE_DAYS contains an unknown day {name!r}. "
                f"Valid names are: {', '.join(WEEKDAY_NAMES)}."
            )
        days.add(WEEKDAY_NAMES.index(key))
    return frozenset(days)


# Same reasoning as validate_window above: a typo stops the container from
# booting rather than degrading into silence on the day it was meant to cover.
ALWAYS_ACTIVE_WEEKDAYS = validate_days(BOT_ALWAYS_ACTIVE_DAYS)


# ============================================================
# Timezone normalisation — the ONE place a datetime is converted
# ============================================================
def _to_dhaka(dt: datetime) -> datetime:
    """
    Normalise `dt` to Dhaka-local before any field is read off it.

    An aware datetime is converted; a naive one is assumed to already be
    Dhaka-local and returned unchanged (the documented contract).

    Both predicates go through here so the hour check and the weekday check
    can never end up judged in different zones. The weekday failure that
    causes is invisible under the shipped window — see the block comment in
    tests/test_active_hours.py.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(DHAKA)
    return dt


# ============================================================
# The pure predicate
# ============================================================
def is_within_active_hours(
    dt: datetime,
    start_hour: int | None = None,
    end_hour: int | None = None,
) -> bool:
    """
    Return True if `dt` falls inside the active window.

    Boundaries are start-inclusive, end-exclusive, at hour granularity:
    with the default 23 -> 9 window the bot is active from 23:00:00 through
    08:59:59 and inert from 09:00:00. Minutes never enter the comparison.

    Args:
        dt: The instant to test. If timezone-aware it is converted to
            Asia/Dhaka first, so a UTC instant is judged by its Dhaka-local
            hour. If naive it is assumed to already be Dhaka-local.
        start_hour: Override for BOT_ACTIVE_START_HOUR (0-23).
        end_hour: Override for BOT_ACTIVE_END_HOUR (0-24, 24 = end of day).
    """
    if start_hour is None:
        start_hour = BOT_ACTIVE_START_HOUR
    if end_hour is None:
        end_hour = BOT_ACTIVE_END_HOUR

    validate_window(start_hour, end_hour)

    hour = _to_dhaka(dt).hour

    # Same-day window, e.g. 9 -> 17. Also covers every N -> 24 window,
    # since no hour is ever >= 24.
    if start_hour < end_hour:
        return start_hour <= hour < end_hour

    # Wrapping window, e.g. 23 -> 9: active late on one calendar day and
    # early on the next. start == end is impossible here (validated above).
    return hour >= start_hour or hour < end_hour


# ============================================================
# The always-active-day predicate
# ============================================================
def is_always_active_day(dt: datetime, days=None) -> bool:
    """
    Return True if `dt` falls on a configured always-active weekday.

    Whole calendar days in Asia/Dhaka: from 00:00:00 through 23:59:59 local.

    Args:
        dt: The instant to test, same timezone contract as
            is_within_active_hours — aware is converted, naive is assumed
            Dhaka-local.
        days: Override for BOT_ALWAYS_ACTIVE_DAYS, a sequence of day names.
    """
    resolved = ALWAYS_ACTIVE_WEEKDAYS if days is None else validate_days(days)
    return _to_dhaka(dt).weekday() in resolved


# ============================================================
# The combined predicate
# ============================================================
def is_bot_active_at(
    dt: datetime,
    start_hour: int | None = None,
    end_hour: int | None = None,
    days=None,
) -> bool:
    """
    Return True if the bot should act on an event at `dt`.

    Active = inside the hour window OR on an always-active weekday. The OR
    is what makes Thu 23:00 -> Sat 09:00 one continuous stretch with a
    Friday configured: the window carries Thu 23:00 -> Fri 09:00, the day
    rule carries all of Friday, and the window carries Sat 00:00 -> 09:00.
    """
    return is_within_active_hours(dt, start_hour, end_hour) or is_always_active_day(
        dt, days
    )


def describe_schedule() -> str:
    """
    One-line summary of the whole rule set, for logs.

    The gate's log line is what someone reads when asking "why was the bot
    silent on Friday", so it must state every rule in force, not just the
    window.
    """
    window = f"{BOT_ACTIVE_START_HOUR:02d}:00-{BOT_ACTIVE_END_HOUR:02d}:00 Asia/Dhaka"
    if not ALWAYS_ACTIVE_WEEKDAYS:
        return window
    names = ", ".join(WEEKDAY_NAMES[i] for i in sorted(ALWAYS_ACTIVE_WEEKDAYS))
    return f"{window}, plus all day: {names}"


# ============================================================
# The clock seam
# ============================================================
def now_in_dhaka() -> datetime:
    """Current time as a Dhaka-local aware datetime, ignoring host TZ."""
    return datetime.now(DHAKA)


def bot_is_active() -> bool:
    """Return True if the bot should act on events right now."""
    return is_bot_active_at(now_in_dhaka())
