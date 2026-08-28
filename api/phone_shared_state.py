"""
Phone-shared state — tracks which customers have already given us their number.

Once a customer shares a mobile number, every subsequent reply that still ends
with "share your mobile number" reads as the bot having lost the conversation.
This module remembers who has shared, so api/messenger.py can strip that ask
out of the reply on its way to the Send API.

Storage: in-memory dict, process-local, mirroring api/pause_state.py. The flag
never expires within a process lifetime — it dies on restart like everything
else. That is the same missing-persistence gap pause_state has, now with a
second consumer: a restart mid-window means the bot re-asks every customer who
had already answered. See the SQLite item in the README roadmap.

The stored value is a timestamp rather than a bare set membership. Nothing
reads it today; it is there so a TTL can be added later without changing the
shape of the dict or any call site.
"""
import time
import logging
from typing import Dict

log = logging.getLogger(__name__)

# ============================================================
# State (process-local, in-memory)
# ============================================================
# Maps customer PSID -> unix timestamp of the first/most recent share.
_shared: Dict[str, float] = {}


# ============================================================
# Public API
# ============================================================
def mark_phone_shared(customer_id: str, reason: str = "customer_message") -> None:
    """
    Record that this customer has shared a phone number.

    Called from two places, deliberately overlapping:
      - reason="customer_message": messenger detected a number in the raw
        inbound text, before any branch could consume the event
      - reason="generator_bypass": Generator.generate() returned
        PHONE_ACKNOWLEDGMENT, i.e. its own detector fired on the sanitised text
    """
    if not customer_id:
        log.warning("mark_phone_shared called with empty customer_id")
        return

    if customer_id in _shared:
        # Already known — refresh the timestamp but stay quiet in the log.
        _shared[customer_id] = time.time()
        return

    _shared[customer_id] = time.time()
    log.info(
        f"Phone number recorded for customer {customer_id[:10]}... "
        f"(reason={reason}) — CTA suppressed from future replies"
    )


def has_shared_phone(customer_id: str) -> bool:
    """Return True if this customer has already given us a phone number."""
    if not customer_id:
        return False
    return customer_id in _shared


def get_shared_count() -> int:
    """Return number of customers flagged as having shared. For /health."""
    return len(_shared)


def clear_all() -> None:
    """Clear all phone-shared state. Useful for tests; never call in production."""
    _shared.clear()
    log.warning("All phone-shared state cleared")
