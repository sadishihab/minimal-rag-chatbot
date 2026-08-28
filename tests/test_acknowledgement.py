"""
Tests for is_acknowledgement() in api/message_classifier.py.
Run with: pytest tests/test_acknowledgement.py -v

A real pytest suite, deliberately NOT added to tests/test_message_classifier.py:
that file's checks live under `if __name__ == "__main__"`, so pytest collects
zero tests from it and reports success (known issue 1 in CLAUDE.md). A guard
that only runs when someone remembers to invoke a script by hand is not a guard.

WHAT WOULD MAKE THIS SUITE GO RED — the mutations it exists to catch, each
verified by actually breaking the implementation and watching it fail rather
than by reasoning that it should. Counts are whole-repo `pytest tests/`:

  1. `return True`   → 60 failures, from test_not_an_acknowledgement.
  2. `return False`  → 67 failures, from test_every_literal_matches.
  3. prefix matching (`normalised.startswith(a)`)  → 30 failures.
  4. substring matching (`a in normalised`)        → 32 failures.
  5. stripping emoji ANYWHERE, not just the edges  →  3 failures, from
     test_emoji_in_the_middle_of_the_message_is_not_stripped.
  6. stripping the trailing edge only, as before   →  7 failures, from
     test_a_leading_emoji_run_is_stripped.

(3) and (4) are the ones that matter, and they fail on
test_a_list_item_followed_by_more_text_does_not_match — "ok koto lagbe?" by
name. Whole-message matching is the rule that makes the branch safe to run in
front of the pipeline, so it gets a dedicated block rather than a couple of
incidental cases. (5) guards the one relaxation of that rule.

(5) is subtler than it looks and the obvious case does not test it:
"ok 👍 koto lagbe?" reduces to "ok koto lagbe?" under anywhere-stripping, which
is still not on the list, so it returns False under BOTH implementations. Only
the mid-WORD cases ("o👍k") separate them, which is why they are in the suite.

Deleting the routing branch in api/messenger.py while leaving this predicate
intact is a seventh mutation (3 failures), and it is invisible here by
construction — the tests that catch it live in tests/test_messenger_gate.py.
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


@pytest.mark.parametrize(
    "text",
    [
        "ok 👍",
        "thanks 👍👍",
        "ঠিক আছে 🙏",
        "thanks 😊",
        "ধন্যবাদ 🙏🙏🙏",
        "okay😊",              # no space before the emoji
        "ok 👍.",              # emoji then punctuation
        "ok. 👍",              # punctuation then emoji — the loop interleaves
    ],
)
def test_a_trailing_emoji_run_is_stripped(text):
    """A trailing 👍 on an acknowledgement is close to universal on Messenger."""
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "👍 ok",
        "🙏 ঠিক আছে",
        "👍 ok 👍",            # both ends at once
        "👍👍 thanks",
        "😊 thanks",
        "👍ok",                # no space after the emoji
        "👍 ok.",              # leading emoji, trailing punctuation
    ],
)
def test_a_leading_emoji_run_is_stripped(text):
    """
    "👍 ok" is as common as "ok 👍". Emoji carry no meaning that could turn an
    acknowledgement into a question, so taking them off either end cannot
    swallow anything — the remainder still has to be an exact whole-message
    match.
    """
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ok 👍🏽",              # skin tone modifier
        "ok 🇧🇩",              # regional indicators (flag)
        "ok 👨\u200d👩\u200d👧\u200d👦",  # ZWJ family sequence
        "thanks ❤️",           # variation selector
    ],
)
def test_the_emoji_predicate_is_reused_not_reinvented(text):
    """
    Skin tones, regional indicators, ZWJ sequences and variation selectors are
    all things _is_emoji_char knows about and a naive category=="So" check does
    not. They work here only because the strip reuses that predicate — which is
    what stops the two branches disagreeing about what an emoji is.
    """
    assert is_acknowledgement(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ok bhai",             # a WORD is not noise
        "thanks bhai",
        "ok 👍 bhai",          # emoji stripped, the word still disqualifies it
        "👍 ok bhai",
        "ঠিক আছে apu",
        "ok vai 👍",
    ],
)
def test_a_word_is_never_noise(text):
    """
    THE LINE: emoji are punctuation-like, words are not.

    "ok bhai" was raised twice and refused twice — deliberately, not
    overlooked. Honorifics (bhai, apu, vai, bro, ji, sir) have no natural end
    as a list, and each one added is a step away from whole-message matching,
    which is the property that makes running this in front of the pipeline safe
    at all. Emoji are exempt because they cannot change what a message asks; a
    word can.

    Pinned rather than remembered, because "ok bhai" and "ok 👍" look like the
    same shape and are not.
    """
    assert is_acknowledgement(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "ok 👍 koto lagbe?",   # mid-message, and still a question
        "👍 ok koto lagbe?",   # leading emoji stripped, still a question
        "ঠিক আছে 👍 কিন্তু দাম কত",
        "o👍k",                # inside a word — not an edge
        "th👍anks",
        "ঠিক আ🙏ছে",
        "thank 👍 you",        # between the two words of a literal
    ],
)
def test_emoji_in_the_middle_of_the_message_is_not_stripped(text):
    """
    Both ends is NOT the same as everywhere, and this is the block that keeps
    the two apart. A "strip emoji anywhere" implementation turns "o👍k" into a
    match; the edge-only one leaves it alone.

    Worth being precise about, because the obvious discriminator is not one:
    "ok 👍 koto lagbe?" reduces to "ok koto lagbe?" under anywhere-stripping,
    which is still not on the list, so it stays False either way. The mid-WORD
    cases are what actually separate the two implementations.
    """
    assert is_acknowledgement(text) is False


@pytest.mark.parametrize("text", ["👍", "🙏🙏", "❤️", "😊 😊"])
def test_emoji_alone_is_not_an_acknowledgement(text):
    """
    Stripping leaves nothing, and nothing is not on the list. So the emoji-only
    branch wins on ordering AND this predicate declines independently — two
    reasons, either sufficient. test_inside_window_emoji_takes_the_emoji_branch
    in tests/test_messenger_gate.py pins which branch actually runs.
    """
    assert is_acknowledgement(text) is False
