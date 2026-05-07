"""
Pause state — tracks which customer threads have been taken over by a human rep.

When a customer service rep replies in Page Inbox, we mark that customer's
thread as paused. While paused, the bot stays silent for text messages and
only sends a handoff acknowledgment for attachments (so the customer knows
their image/file was received).

The pause uses a sliding 7-day window — each rep reply resets the clock.

Storage: in-memory dict for development. Will move to SQLite when deployed
to DigitalOcean. State is intentionally lost on uvicorn restart in dev —
worst case is the bot resumes for a thread that should still be paused,
which the rep will quickly correct on their next reply (self-healing).
"""
import time
import logging
from typing import Dict

log = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================
PAUSE_DURATION_SECONDS = 7 * 24 * 60 * 60  # 7 days

# ============================================================
# State (process-local, in-memory)
# ============================================================
# Maps customer PSID -> unix timestamp of last rep reply.
# Module-level dict survives across requests within one uvicorn process.
_paused: Dict[str, float] = {}


# ============================================================
# Public API
# ============================================================
def pause_thread(customer_id: str, reason: str = "rep_reply") -> None:
    """
    Pause the bot for this customer thread for 7 days.
    Resets the 7-day window on every call (sliding window).

    Called when:
      - A rep replies via Page Inbox (reason="rep_reply")
      - Customer sends an attachment (reason="attachment")
      - Customer sends a URL (reason="url")
    """
    if not customer_id:
        log.warning("pause_thread called with empty customer_id")
        return

    now = time.time()
    was_paused = customer_id in _paused
    _paused[customer_id] = now

    if was_paused:
        log.info(
            f"Pause refreshed for customer {customer_id[:10]}... "
            f"(reason={reason}, sliding window reset)"
        )
    else:
        log.info(
            f"Thread PAUSED for customer {customer_id[:10]}... "
            f"(reason={reason}, 7 days)"
        )


def is_paused(customer_id: str) -> bool:
    """
    Return True if this customer's thread is currently in rep-takeover mode.
    Lazily evicts expired entries on read (no background thread needed).
    """
    if not customer_id:
        return False

    ts = _paused.get(customer_id)
    if ts is None:
        return False

    age_seconds = time.time() - ts
    if age_seconds < PAUSE_DURATION_SECONDS:
        return True

    # Expired — clean up and resume
    del _paused[customer_id]
    log.info(
        f"Pause expired for customer {customer_id[:10]}... "
        f"(no rep activity for {age_seconds/86400:.1f} days) — bot resuming"
    )
    return False


def get_pause_count() -> int:
    """Return number of currently-paused threads. Useful for /health endpoint."""
    return len(_paused)


def clear_all() -> None:
    """Clear all pause state. Useful for tests; never call in production."""
    _paused.clear()
    log.warning("All pause state cleared")