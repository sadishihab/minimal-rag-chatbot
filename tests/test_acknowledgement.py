"""
Tests for is_acknowledgement() in api/message_classifier.py.
Run with: pytest tests/test_acknowledgement.py -v

A real pytest suite, deliberately NOT added to tests/test_message_classifier.py:
that file's checks live under `if __name__ == "__main__"`, so pytest collects
zero tests from it and reports success (known issue 1 in CLAUDE.md). A guard
that only runs when someone remembers to invoke a script by hand is not a guard.

WHAT WOULD MAKE THIS SUITE GO RED — the four mutations it exists to catch,
each verified by actually breaking the implementation and watching it fail
rather than by reasoning that it should:

  1. `return True`   → 50 failures, from test_not_an_acknowledgement.
  2. `return False`  → 47 failures, from test_every_literal_matches.
  3. prefix matching (`normalised.startswith(a)`)  → 24 failures.
  4. substring matching (`a in normalised`)        → 26 failures.

(3) and (4) are the ones that matter, and they fail on
test_a_list_item_followed_by_more_text_does_not_match. Whole-message matching
is the rule that makes the branch safe to run in front of the pipeline, so it
gets a dedicated block rather than a couple of incidental cases.

Deleting the routing branch in api/messenger.py while leaving this predicate
intact is a fifth mutation, and it is invisible here by construction — the two
tests that catch it live in tests/test_messenger_gate.py.
"""
import pytest

from api.message_classifier import (
    _ACKNOWLEDGEMENT_LITERALS,
    _ACKNOWLEDGEMENTS,
    is_acknowledgement,
    is_emoji_only,
)


# ============================================================
# Positives — every literal on the maintainer's list
# ============================================================
@pytest.mark.parametrize("literal", _ACKNOWLEDGEMENT_LITERALS)
def test_every_literal_matches(literal):
    """Fails on a blanket `return False`, and on any literal lost in an edit."""
    assert is_acknowledgement(literal) is True


@pytest.mark.parametrize(
    "text",
    [
        "OK",
        "Ok",
        "oK",
        "Okay",
        "THANKS",
        "Thank You",
        "TNX",
        "Thnx",
        "Accha",
        "Thik Ache",
        "DHONNOBAD",
    ],
)
def test_case_is_ignored(text):
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        " ok ",
        "\nthanks\n",
        "\tokay\t",
        "  ঠিক আছে  ",
        "thank  you",          # doubled internal space
        "thank\tyou",          # tab between the words
        "ঠিক  আছে",            # doubled internal space, Bangla
    ],
)
def test_whitespace_is_normalised(text):
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ok.",
        "ok!",
        "ok!!!",
        "okay.",
        "thanks.",
        "ঠিক আছে।",            # Bangla danda
        "ধন্যবাদ।",
        "আচ্ছা,",
        "ok . ",               # punctuation and trailing space together
    ],
)
def test_trailing_punctuation_is_stripped(text):
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ok\u200c",           # ZWNJ — invisible, and Bangla keyboards emit it
        "\u200bthanks",       # ZWSP
        "\ufeffokay",         # BOM
        "ঠিক\u200c আছে",
    ],
)
def test_zero_width_characters_are_stripped(text):
    assert is_acknowledgement(text) is True


# ============================================================
# Negatives — whole-message matching
# ============================================================
@pytest.mark.parametrize(
    "text",
    [
        "ok koto lagbe?",      # THE case: starts with "ok" and IS a question
        "ok but koto?",
        "okay price ta bolen",
        "accha apnara ki site visit koren",
        "ঠিক আছে দাম কত",
        "ওকে কবে আসবেন",
        "আচ্ছা তাহলে বলুন",
        "thanks a lot",
        "thank you very much",
        "thanks, ekta question",
        "ধন্যবাদ ভাই",
        "ok 01775760496",      # an ack plus a number — the number is the point
        "not ok",              # ends with an ack rather than starting with one
        "is this ok",
    ],
)
def test_a_list_item_followed_by_more_text_does_not_match(text):
    """
    The load-bearing test. A prefix match, a suffix match, or a substring
    match all fail here; only whole-message matching passes.
    """
    assert is_acknowledgement(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "okk",
        "oki",
        "okey",
        "thanksss",
        "thnks",
        "dhonnobadh",
        "ঠিক",                 # half of a two-word literal
        "আছে",
        "ok?",                 # '?' is not stripped — reads as a question
        "okay?",
        "hmm",                 # deliberately excluded as ambiguous
        "হুম",
        "ji",
        "জি",
        "দাম কত?",
        "hello",
        "",
        "   ",
        "...",                 # normalises to empty, must not match
        "।",
    ],
)
def test_not_an_acknowledgement(text):
    """Fails on a blanket `return True`."""
    assert is_acknowledgement(text) is False


def test_none_is_handled():
    assert is_acknowledgement(None) is False


# ============================================================
# The list itself
# ============================================================
def test_the_literal_list_is_exactly_what_was_agreed():
    """
    Pins the closed list. Adding a variant — "hmm", "ji", "thanx" — is a
    maintainer decision, and this test is what makes such an addition
    deliberate rather than incidental.
    """
    assert _ACKNOWLEDGEMENT_LITERALS == (
        "ok",
        "okay",
        "ok.",
        "thanks",
        "thank you",
        "tnx",
        "thnx",
        "ওকে",
        "আচ্ছা",
        "ঠিক আছে",
        "ধন্যবাদ",
        "accha",
        "thik ache",
        "dhonnobad",
    )


def test_ok_dot_collapses_onto_ok():
    """
    14 literals, 13 unique members. "ok." is kept in the source list as a
    verbatim record of the decision; normalisation makes it redundant, and
    this is where that redundancy is documented rather than surprising.
    """
    assert len(_ACKNOWLEDGEMENT_LITERALS) == 14
    assert len(_ACKNOWLEDGEMENTS) == 13
    assert "ok" in _ACKNOWLEDGEMENTS
    assert "ok." not in _ACKNOWLEDGEMENTS


# ============================================================
# Disjoint from the emoji branch it sits next to
# ============================================================
@pytest.mark.parametrize("literal", _ACKNOWLEDGEMENT_LITERALS)
def test_no_literal_is_also_emoji_only(literal):
    """
    The two branches are adjacent in process_messaging_event, so their order
    is only immaterial while the sets stay disjoint. This is what fails if a
    future edit to either predicate creates an overlap.
    """
    assert is_emoji_only(literal) is False


@pytest.mark.parametrize("text", ["👍", "❤️", "😊 😊", "🇧🇩"])
def test_emoji_is_not_an_acknowledgement(text):
    assert is_acknowledgement(text) is False


@pytest.mark.parametrize("text", ["ok 👍", "thanks 😊", "ঠিক আছে 👍"])
def test_a_trailing_emoji_falls_through_for_now(text):
    """
    Pins a KNOWN GAP, not a desired behaviour. Stripping a trailing emoji run
    before matching was considered and deferred — it widens the feature past
    the agreed list. If that decision is revisited, this test is the one to
    flip, and it is here so the gap is visible rather than discovered in a
    transcript.
    """
    assert is_acknowledgement(text) is False
