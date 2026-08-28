"""
Tests for api/cta_substitution.py — the string surgery itself.
Run with: pytest tests/test_cta_substitution.py -v

No knowledge base, no FAISS index, no OPENAI_API_KEY, no network. Everything
here is built out of the constants the production module actually uses, so a
KB edit that breaks the real substitution cannot leave these green — see
tests/audit_cta_variants.py for the other half of that guard.

Several tests below are POSITIVE CONTROLS: they exist to fail if the
substitution or the drift warning becomes a no-op. A suite where nothing is
ever left alone and nothing is ever warned about would pass every assertion
about things that were replaced.
"""
import logging

import pytest

from api.cta_substitution import (
    CTA_FULL,
    CTA_SCHEDULING,
    CTA_SHORT,
    PHONE_MENTION,
    REPLACEMENT_GENERIC,
    REPLACEMENT_SCHEDULING,
    check_for_drift,
    substitute_cta,
)
from generation.phone_detector import PHONE_ACKNOWLEDGMENT

CUSTOMER = "CUSTOMER_PSID_1234567890"

# The lead-in that CTA_FULL adds on top of CTA_SHORT. If CTA_SHORT is matched
# first, this is what gets orphaned in front of the replacement.
LEAD_IN = CTA_FULL[: -len(CTA_SHORT)]


# ============================================================
# The relationship the ordering exists to survive
# ============================================================
def test_short_variant_really_is_a_substring_of_the_full_one():
    """
    The premise of the whole ordering requirement. If a future KB edit breaks
    this, the ordering test below stops testing anything and this one says so.
    """
    assert CTA_SHORT in CTA_FULL
    assert CTA_SCHEDULING not in CTA_FULL
    assert LEAD_IN and CTA_FULL == LEAD_IN + CTA_SHORT


def test_substring_ordering_does_not_corrupt_the_full_cta():
    """
    Matching CTA_SHORT before CTA_FULL rewrites only the tail of a full CTA and
    leaves the lead-in stranded in front of the replacement, producing a
    doubled anaphor: "...বিস্তারিত তথ্যের জন্য এ বিষয়ে আমাদের একজন...".

    Asserting the replacement is present is not enough to catch that — the
    corrupted string contains it too. The load-bearing assertion is the last one.
    """
    result = substitute_cta("প্যাকেজের দাম ৯ লাখ টাকা। " + CTA_FULL)

    assert REPLACEMENT_GENERIC in result
    assert CTA_FULL not in result
    assert CTA_SHORT not in result
    # The corruption itself:
    assert LEAD_IN + REPLACEMENT_GENERIC not in result
    assert result == "প্যাকেজের দাম ৯ লাখ টাকা। " + REPLACEMENT_GENERIC


# ============================================================
# Replacement, never truncation
# ============================================================
def test_mid_answer_cta_leaves_the_urls_after_it_intact():
    """
    45 of the 78 full-CTA entries carry URLs after the sentence. Anything that
    cuts from the CTA onward destroys them, which is why this is a replacement
    and not a truncation. Shape copied from the real pricing entries.
    """
    lead = (
        "৳ ১,২০০ থেকে ৳ ২,৫০০ প্রতি বর্গফুট (রিকোয়ারমেন্ট অনুযায়ী)।\n\n"
    )
    tail = (
        "\n\nপ্যাকেজ বিস্তারিত: https://www.example.com/share/p/AAAA/"
        "\n\nজোন ভিত্তিক দাম: https://www.example.com/share/p/BBBB/"
    )

    result = substitute_cta(lead + CTA_FULL + tail)

    assert result == lead + REPLACEMENT_GENERIC + tail
    assert result.startswith(lead)
    assert result.endswith(tail)
    assert "https://www.example.com/share/p/AAAA/" in result
    assert "https://www.example.com/share/p/BBBB/" in result


def test_interest_signal_shape_is_replaced():
    """The 9 sub_intent=interest_signal answers: thank-you + short CTA."""
    result = substitute_cta("আপনার আগ্রহের জন্য ধন্যবাদ। " + CTA_SHORT)
    assert result == "আপনার আগ্রহের জন্য ধন্যবাদ। " + REPLACEMENT_GENERIC


def test_site_visit_keeps_the_scheduling_promise():
    """
    The 6 intent=site_visit answers get their own replacement. Using the
    generic one here would drop the scheduling promise from a reply to
    "do you do site visits?" — a content regression, not a wording change.
    """
    result = substitute_cta("জ্বি, আমরা বাসায় সাইট ভিজিটে যাই। " + CTA_SCHEDULING)

    assert result == "জ্বি, আমরা বাসায় সাইট ভিজিটে যাই। " + REPLACEMENT_SCHEDULING
    assert REPLACEMENT_GENERIC not in result


def test_scheduling_and_short_share_a_prefix_without_bleeding():
    """
    CTA_SHORT and CTA_SCHEDULING share a 55-character prefix and diverge only
    after "সাপোর্ট ম্যানেজার". Matching is on whole sentences including the
    trailing danda, so neither can eat the other's tail.
    """
    both = CTA_SHORT + " " + CTA_SCHEDULING
    result = substitute_cta(both)
    assert result == REPLACEMENT_GENERIC + " " + REPLACEMENT_SCHEDULING


def test_crash_fallback_is_covered_for_free():
    """
    The messenger crash fallback's second sentence is CTA_SHORT verbatim, so
    the send-boundary substitution catches it with no rule of its own.
    """
    # Kept in sync by hand with the literal in api/messenger.py — the flow
    # test asserts the real one, this one asserts the substitution rule.
    crash = (
        "এই মুহূর্তে একটু সমস্যা হচ্ছে। "
        "আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের সাপোর্ট ম্যানেজার "
        "আপনাকে কল করে সহায়তা করতে পারবেন।"
    )
    assert CTA_SHORT in crash          # the premise
    result = substitute_cta(crash)
    assert result == "এই মুহূর্তে একটু সমস্যা হচ্ছে। " + REPLACEMENT_GENERIC
    assert PHONE_MENTION not in result


def test_substitution_is_idempotent():
    """
    Trivially true while no replacement contains the CTA text. It goes red the
    moment a future replacement wording does, which is the point.
    """
    once = substitute_cta("দাম ৯ লাখ। " + CTA_FULL)
    assert substitute_cta(once) == once


@pytest.mark.parametrize("empty", ["", None])
def test_empty_text_is_returned_unchanged(empty):
    assert substitute_cta(empty) == empty


# ============================================================
# POSITIVE CONTROLS — these fail if substitution stops discriminating
# ============================================================
def test_text_without_a_cta_is_left_completely_alone():
    """
    If substitute_cta ever became "return REPLACEMENT_GENERIC" or started
    matching loosely, this is the test that catches it.
    """
    untouched = (
        "আমাদের অফিস ঢাকার বনানীতে অবস্থিত। "
        "সরাসরি যোগাযোগের জন্য কল করুন: 01775-760496।"
    )
    assert substitute_cta(untouched) == untouched


def test_a_reworded_cta_is_not_substituted_and_does_warn(caplog):
    """
    THE mutation control. A CTA the model paraphrased by one character must
    NOT be silently rewritten — exact matching is the whole design — and it
    MUST show up as drift, because that log line is the instrument measuring
    how often the model fails to reproduce KB text verbatim.

    A substitution that quietly handled this would be matching loosely, and a
    drift check that stayed quiet would be measuring nothing.
    """
    mutated = CTA_FULL.replace("সহায়তা", "সহযোগিতা")
    assert mutated != CTA_FULL

    result = substitute_cta("দাম ৯ লাখ। " + mutated)
    assert result == "দাম ৯ লাখ। " + mutated      # untouched
    assert REPLACEMENT_GENERIC not in result

    with caplog.at_level(logging.WARNING):
        assert check_for_drift(result, CUSTOMER) is True
    assert "CTA drift" in caplog.text
    assert mutated in caplog.text                  # the wording is recoverable


def test_a_substituted_reply_does_not_warn(caplog):
    """
    The other half of the control. Without this, a drift check hardwired to
    warn every time would look exactly like a working one.
    """
    result = substitute_cta("দাম ৯ লাখ। " + CTA_FULL)
    with caplog.at_level(logging.WARNING):
        assert check_for_drift(result, CUSTOMER) is False
    assert caplog.text == ""


def test_phone_acknowledgment_does_not_trip_the_drift_check(caplog):
    """
    PHONE_ACKNOWLEDGMENT opens "মোবাইল নম্বর শেয়ার করার জন্য ধন্যবাদ" — it
    thanks the customer for the number rather than asking for one. A customer
    who shares a number twice would otherwise generate a warning every time,
    and a signal that cries wolf is not a signal.
    """
    assert PHONE_MENTION in PHONE_ACKNOWLEDGMENT   # the premise
    with caplog.at_level(logging.WARNING):
        assert check_for_drift(PHONE_ACKNOWLEDGMENT, CUSTOMER) is False
    assert caplog.text == ""
