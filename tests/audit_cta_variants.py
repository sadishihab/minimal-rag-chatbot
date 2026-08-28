"""
KB hygiene: every "share your mobile number" sentence in the knowledge base
must still be one of the three variants api/cta_substitution.py knows about.
Run with: pytest tests/audit_cta_variants.py -v

This is the half of the guard that lives outside the code. The substitution
matches exact strings lifted from data/knowledge_base.json, which is
gitignored, proprietary, and hand-edited as the main ongoing work — so a
single re-worded answer silently drops out of coverage with nothing failing.
Production would show it only as a CTA drift WARNING, after a real customer
saw the wrong reply. This turns it into a red test at KB-edit time.

The real KB is not in the repo, so this SKIPS on a fresh clone rather than
failing. Recover it from a running container if you need it:
    docker exec minimal-rag cat /app/data/knowledge_base.json > data/knowledge_base.json
"""
import json

import pytest

from api.cta_substitution import (
    CTA_FULL,
    CTA_SCHEDULING,
    CTA_SHORT,
    PHONE_MENTION,
)
from config import KNOWLEDGE_BASE_PATH

# Counts as of the 336-entry production KB. Asserted loosely (as a floor plus
# an exact total) rather than pinned per-variant, so that adding KB entries
# does not fail the build while a rewording still does.
KNOWN_VARIANTS = (CTA_FULL, CTA_SHORT, CTA_SCHEDULING)


@pytest.fixture(scope="module")
def answers():
    if not KNOWLEDGE_BASE_PATH.exists():
        pytest.skip(f"real knowledge base not present at {KNOWLEDGE_BASE_PATH}")
    kb = json.loads(KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8"))
    return [(e.get("id"), e.get("answer") or "") for e in kb["entries"]]


def test_every_phone_ask_in_the_kb_is_a_known_variant(answers):
    """
    An answer that mentions মোবাইল নম্বর but matches none of the three
    constants is invisible to substitution. Report the ids so the drift can
    be found in one grep.
    """
    unmatched = [
        entry_id
        for entry_id, answer in answers
        if PHONE_MENTION in answer
        and not any(variant in answer for variant in KNOWN_VARIANTS)
    ]
    assert not unmatched, (
        f"{len(unmatched)} KB answer(s) ask for a phone number in wording "
        f"api/cta_substitution.py does not know: {unmatched}"
    )


def test_variant_counts_are_still_in_the_expected_shape(answers):
    """
    A positive control on the fixture itself. If this file ever loaded an
    empty or wrong KB, the test above would pass by finding nothing at all.
    """
    full = sum(a.count(CTA_FULL) for _, a in answers)
    short_only = sum(a.count(CTA_SHORT) for _, a in answers) - full
    scheduling = sum(a.count(CTA_SCHEDULING) for _, a in answers)

    assert full >= 78, f"full CTA occurrences dropped to {full} (was 78)"
    assert short_only >= 9, f"short CTA occurrences dropped to {short_only} (was 9)"
    assert scheduling >= 6, f"scheduling CTA occurrences dropped to {scheduling} (was 6)"

    mentions = sum(1 for _, a in answers if PHONE_MENTION in a)
    assert full + short_only + scheduling == mentions, (
        "an answer contains more than one phone ask, or a variant overlaps "
        "another — the substitution assumes exactly one per answer"
    )
