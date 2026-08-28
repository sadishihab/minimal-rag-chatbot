"""
CTA substitution — drop the "share your mobile number" ask from an outbound
reply once that customer has already shared one.

The ask is not a single code constant. It lives in 93 knowledge-base answers
in three wordings, and the model reproduces whichever one retrieval fed it.
Two more copies are injected by prompt_builder's ambiguous-query and
knowledge-base-miss instructions, and a third sits in the messenger crash
fallback — all three of those emit text identical to one of the KB wordings,
so matching the KB wordings covers them without a separate rule.

REPLACEMENT, NEVER TRUNCATION. 45 of the 78 full-CTA answers carry URLs after
the sentence; anything that cuts from the CTA onward destroys them.

This module is deliberately pure: it knows nothing about PSIDs or about who
has shared a number. The caller (api/messenger.py) owns that decision, which
is what keeps generation/ identity-free.
"""
import logging
import unicodedata

from generation.phone_detector import PHONE_ACKNOWLEDGMENT

log = logging.getLogger(__name__)


# ============================================================
# The ask, as it appears in the knowledge base
# ============================================================
# Customer-facing Bangla copy — owned by the maintainer, not by this code.
# These are verbatim from data/knowledge_base.json (gitignored/proprietary);
# tests/audit_cta_variants.py fails if the KB drifts away from them.

# 78 occurrences, 12 intents, all three language rows.
CTA_FULL = "এই বিষয়ে বিস্তারিত তথ্যের জন্য আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের সাপোর্ট ম্যানেজার আপনাকে কল করে সহায়তা করতে পারবেন।"

# 9 occurrences, all sub_intent=interest_signal.
# NOTE: this is a SUBSTRING of CTA_FULL. See _SUBSTITUTIONS below.
CTA_SHORT = "আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের সাপোর্ট ম্যানেজার আপনাকে কল করে সহায়তা করতে পারবেন।"

# 6 occurrences, all intent=site_visit.
CTA_SCHEDULING = "আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের সাপোর্ট ম্যানেজার কল করে শিডিউল করবেন।"


# ============================================================
# What replaces it
# ============================================================
REPLACEMENT_GENERIC = "এ বিষয়ে আমাদের একজন প্রতিনিধি আপনাকে কল করে বিস্তারিত জানাবেন।"

# site_visit gets its own line: the generic replacement drops the scheduling
# promise, which is a content regression rather than a wording change.
REPLACEMENT_SCHEDULING = "এ বিষয়ে আমাদের একজন প্রতিনিধি আপনাকে কল করে সাইট ভিজিট শিডিউল করবেন।"


# ============================================================
# Substitution table
# ============================================================
# Ordered LONGEST PATTERN FIRST, and sorted rather than hand-ordered so a
# variant added later cannot be mis-ordered by accident.
#
# This ordering is load-bearing: CTA_SHORT is a substring of CTA_FULL, so
# matching CTA_SHORT first would rewrite the tail of every full CTA and leave
# the orphaned lead-in behind, producing a doubled anaphor
# ("...তথ্যের জন্য এ বিষয়ে..."). test_substring_ordering pins this.
_SUBSTITUTIONS = tuple(
    sorted(
        (
            (CTA_FULL, REPLACEMENT_GENERIC),
            (CTA_SHORT, REPLACEMENT_GENERIC),
            (CTA_SCHEDULING, REPLACEMENT_SCHEDULING),
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)

# Substring used only for drift detection, never for substitution.
PHONE_MENTION = "মোবাইল নম্বর"


def substitute_cta(text: str) -> str:
    """
    Return `text` with every known phone-number CTA replaced.

    Call this ONLY for a customer who has already shared a number — it is the
    caller's job to check. Text with no CTA comes back unchanged.

    The reply is model output, not raw KB text, so it carries no normalisation
    guarantee; the KB side is already NFC. Normalising here means a reply that
    is merely decomposed still matches.
    """
    if not text:
        return text

    result = unicodedata.normalize("NFC", text)
    for cta, replacement in _SUBSTITUTIONS:
        result = result.replace(cta, replacement)
    return result


def check_for_drift(text: str, customer_id: str) -> bool:
    """
    Log a WARNING if `text` still asks a phone-shared customer for their number.

    This is the instrument, not a guard: it does not modify the reply. The
    design bets on the model reproducing KB text verbatim so that exact-string
    substitution is enough. Every line this emits is a case where that bet did
    not pay, with the reply text attached so the wording can be recovered.

    Returns True if drift was detected (for tests; callers ignore it).
    """
    if not text:
        return False

    # PHONE_ACKNOWLEDGMENT opens with "মোবাইল নম্বর শেয়ার করার জন্য ধন্যবাদ" —
    # it thanks the customer for the number rather than asking for one. A
    # customer who shares a number twice would otherwise trip this every time.
    if unicodedata.normalize("NFC", text) == unicodedata.normalize(
        "NFC", PHONE_ACKNOWLEDGMENT
    ):
        return False

    if PHONE_MENTION not in text:
        return False

    log.warning(
        f"CTA drift: reply to phone-shared customer {customer_id[:10]}... still "
        f"mentions {PHONE_MENTION!r} after substitution: {text!r}"
    )
    return True
