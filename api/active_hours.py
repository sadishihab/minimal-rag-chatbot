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

from config import BOT_ACTIVE_END_HOUR, BOT_ACTIVE_START_HOUR, BOT_TIMEZONE

log = logging.getLogger(__name__)

# Resolved once at import — ZoneInfo instances are immutable and cacheable.
DHAKA = ZoneInfo(BOT_TIMEZONE)

# END_HOUR accepts 24 as "end of day" so that 0 -> 24 is an explicit,
# writable always-active window. START_HOUR has no such need: 0 already
# means midnight.
MIN_HOUR = 0
MAX_START_HOUR = 23
MAX_END_HOUR = 24


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

    if dt.tzinfo is not None:
        dt = dt.astimezone(DHAKA)

    hour = dt.hour

    # Same-day window, e.g. 9 -> 17. Also covers every N -> 24 window,
    # since no hour is ever >= 24.
    if start_hour < end_hour:
        return start_hour <= hour < end_hour

    # Wrapping window, e.g. 23 -> 9: active late on one calendar day and
    # early on the next. start == end is impossible here (validated above).
    return hour >= start_hour or hour < end_hour


# ============================================================
# The clock seam
# ============================================================
def now_in_dhaka() -> datetime:
    """Current time as a Dhaka-local aware datetime, ignoring host TZ."""
    return datetime.now(DHAKA)


def bot_is_active() -> bool:
    """Return True if the bot should act on events right now."""
    return is_within_active_hours(now_in_dhaka())
