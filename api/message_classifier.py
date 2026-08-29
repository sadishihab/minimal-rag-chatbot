"""
Message classifier — detect special message shapes that need custom handling.

Currently used to distinguish:
  - Emoji-only text   → reply "ধন্যবাদ" (no pause)
  - Acknowledgement text ("ok", "thanks", "আচ্ছা") → reply "ধন্যবাদ" (no pause)
  - Sticker-only attachments → reply "ধন্যবাদ" (no pause)
  - Anything else → handled by existing routing in messenger.py

All three are conceptually "customer expressed positive engagement without
asking a question," so we acknowledge but don't trigger expensive RAG flow
or rep handover.
"""
import unicodedata


# ============================================================
# Emoji detection
# ============================================================
# Unicode "Other Symbol" (So) covers most emoji.
# Other Punctuation (Po) covers things like ‼️ and …
# We also explicitly allow ZWJ, variation selectors, skin tone modifiers,
# and a few specific characters that are emoji but live in odd categories.

# Characters that are part of emoji sequences but aren't themselves "emoji-like"
# in Unicode category terms (zero-width joiners, variation selectors, modifiers):
_EMOJI_GLUE_CODEPOINTS = {
    0x200D,  # ZWJ (Zero Width Joiner) — joins emoji into compounds like 👨‍👩‍👧
    0xFE0F,  # VS-16 (variation selector) — turns ❤ into ❤️ (emoji presentation)
    0xFE0E,  # VS-15 (text presentation, rare)
}

# Skin tone modifiers (U+1F3FB through U+1F3FF)
_SKIN_TONE_RANGE = range(0x1F3FB, 0x1F400)


def _is_emoji_char(ch: str) -> bool:
    """
    Return True if a single character is emoji-like.

    Counts as emoji:
      - Unicode "Other Symbol" (So) — most emoji live here
      - Skin tone modifiers (🏻🏼🏽🏾🏿)
      - ZWJ and variation selectors (the "glue" between emoji codepoints)
      - Regional indicator letters (used to build flag emoji like 🇧🇩)

    Does NOT count:
      - Letters (Bangla, Latin, etc.) — including Bangla numerals like ০-৯
      - Digits, punctuation other than emoji-y ones
      - ASCII symbols like @ # $ %
    """
    if not ch:
        return False

    cp = ord(ch)

    # ZWJ / variation selectors / explicit "glue" codepoints
    if cp in _EMOJI_GLUE_CODEPOINTS:
        return True

    # Skin tone modifiers
    if cp in _SKIN_TONE_RANGE:
        return True

    # Regional indicators (for flag emoji)
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return True

    category = unicodedata.category(ch)
    # 'So' (Other Symbol) is where most emoji live
    if category == "So":
        return True

    return False


def is_emoji_only(text: str) -> bool:
    """
    Return True if the text contains ONLY emoji (and whitespace).
    Empty / whitespace-only strings return False (they belong to the
    "no message" path, not the emoji path).

    Examples:
      "❤️"       → True
      "👍👍👍"   → True
      "😊 😊"    → True (whitespace allowed)
      "👨‍👩‍👧‍👦"  → True (ZWJ family sequence)
      "👍🏽"     → True (with skin tone)
      "🇧🇩"     → True (flag emoji)
      ""        → False
      "   "     → False
      "hello"   → False
      "hi 😊"   → False (mixed)
      "০১২"     → False (Bangla numerals are NOT emoji)
    """
    if not text:
        return False

    stripped = text.strip()
    if not stripped:
        return False

    for ch in stripped:
        if ch.isspace():
            continue
        if not _is_emoji_char(ch):
            return False
    return True


# ============================================================
# Acknowledgement detection
# ============================================================
# "ok" / "thanks" / "আচ্ছা" is a customer closing the conversation, not asking
# anything. Before this branch existed those messages went through the full
# pipeline, retrieved whatever happened to sit nearest in embedding space, and
# came back with a reply that restarted a conversation the customer had just
# ended — for a phone-shared customer, the substituted CTA
# ("এ বিষয়ে আমাদের একজন প্রতিনিধি আপনাকে কল করে বিস্তারিত জানাবেন।").
#
# Same shape as is_emoji_only above: acknowledge with THANKS_MESSAGE, no pause,
# never reach FAISS. Free, instant, and immune to retrieval landing somewhere
# strange.
#
# WHOLE-MESSAGE MATCH ONLY — never a prefix, never a substring. "ok koto
# lagbe?" starts with "ok" and IS a question, so it must fall through to the
# pipeline. That single rule is what makes the branch safe: normalisation can
# only ever collapse a message onto a member of the closed list below, never
# widen the list itself.
#
# The one thing normalisation removes beyond case, whitespace and punctuation
# is a run of emoji at EITHER END — "ok 👍" and "👍 ok" are both
# acknowledgements, and both are everywhere on Messenger. Emoji carry no
# meaning that could turn an acknowledgement into a question, so removing them
# from an edge cannot swallow anything.
#
# Emoji are treated as punctuation-like; words are not. "ok bhai" keeps its
# "bhai" and falls through, and so does "ok 👍 koto lagbe?" — an emoji in the
# MIDDLE is not an edge, and the text around it is still a question. After
# stripping, what remains has to be an exact whole-message match.

# The list is exact and closed. Additions are a maintainer decision — "hmm" /
# "হুম" and "ji" / "জি" were considered and deliberately excluded as ambiguous.
_ACKNOWLEDGEMENT_LITERALS = (
    # English
    "ok",
    "okay",
    "ok.",
    "thanks",
    "thank you",
    "tnx",
    "thnx",
    # Bangla
    "ওকে",
    "আচ্ছা",
    "ঠিক আছে",
    "ধন্যবাদ",
    # Banglish
    "accha",
    "thik ache",
    "dhonnobad",
)

# Invisible characters that Bangla keyboards emit and no log line shows.
# Without stripping them a non-match is unexplainable from the transcript.
_ZERO_WIDTH_CODEPOINTS = dict.fromkeys(
    (
        0x200B,  # ZWSP
        0x200C,  # ZWNJ — typed to break Bangla conjuncts
        0x200D,  # ZWJ
        0xFEFF,  # BOM / zero-width no-break space
    )
)

# '?' is deliberately NOT here: "ok?" reads as a question ("is that ok?") and
# must reach the pipeline. Leading punctuation is likewise left alone — only
# emoji and whitespace come off the front.
_TRAILING_PUNCTUATION = ".!।॥, \t\n"
_LEADING_WHITESPACE = " \t\n"


def _strip_edge_noise(text: str) -> str:
    """
    Remove emoji and whitespace from both ends, and punctuation from the end.

    EDGES ONLY. An emoji in the middle stays put, which is what keeps
    "ok 👍 koto lagbe?" a question — pinned by
    test_emoji_in_the_middle_of_the_message_is_not_stripped.

    A word is never noise. "ok bhai" keeps its "bhai" and therefore does not
    match. That is the line — emoji are punctuation-like, words are not — and
    it is what the whole feature's safety rests on: emoji carry no meaning that
    could turn an acknowledgement into a question, whereas a word does.
    Honorifics ("bhai", "apu", "vai", "bro", "ji", "sir") were raised twice and
    refused twice, deliberately, not overlooked: they have no natural end as a
    list, and each one added is a step away from whole-message matching.

    Emoji are recognised with _is_emoji_char, the same predicate is_emoji_only
    uses, rather than a second definition. That is what makes flags, skin-tone
    modifiers and ZWJ sequences work here for free, and what keeps the two
    branches from disagreeing about what an emoji is.

    Leading punctuation is deliberately left alone — only emoji and whitespace
    are stripped from the front. Everything is stripped in a loop rather than
    in sequence so the kinds can interleave: "ok 👍." and "👍 ok. 👍" both
    reduce to "ok".
    """
    previous = None
    while text != previous:
        previous = text
        text = text.rstrip(_TRAILING_PUNCTUATION)
        while text and _is_emoji_char(text[-1]):
            text = text[:-1]
        text = text.lstrip(_LEADING_WHITESPACE)
        while text and _is_emoji_char(text[0]):
            text = text[1:]
    return text


def _normalise(text: str) -> str:
    """
    Reduce a message to its comparison form.

    Applied to BOTH sides — the incoming text and _ACKNOWLEDGEMENT_LITERALS at
    import — so the two can never drift apart. This is also what makes "ok."
    collapse onto "ok": the literal is kept in the list above as a verbatim
    record of the decision, and lands on the same set member.
    """
    normalised = unicodedata.normalize("NFC", text)
    normalised = normalised.translate(_ZERO_WIDTH_CODEPOINTS)
    normalised = " ".join(normalised.split())          # collapse internal runs
    normalised = _strip_edge_noise(normalised)
    return normalised.casefold()


# 14 literals, 13 unique members — "ok." collapses onto "ok".
# tests/test_acknowledgement.py pins both the count and the membership.
_ACKNOWLEDGEMENTS = frozenset(
    _normalise(literal) for literal in _ACKNOWLEDGEMENT_LITERALS
)


def is_acknowledgement(text: str) -> bool:
    """
    Return True if the WHOLE message is one of the known acknowledgements.

    Case, surrounding whitespace, internal whitespace runs, trailing
    '. ! । ॥ ,', a trailing run of emoji, Unicode composition, and zero-width
    characters are all normalised away first. A question mark is not.

    Examples:
      "Ok"                  → True
      "ok."                 → True  (collapses onto "ok")
      " THANKS "            → True
      "thank  you"          → True
      "ঠিক আছে।"            → True
      "ok 👍"               → True  (edge emoji stripped)
      "👍 ok"               → True
      "👍 ok 👍"            → True
      "thanks 👍👍"          → True
      "ok koto lagbe?"      → False (a question that starts with an ack)
      "thanks a lot"        → False (not a whole-message match)
      "ok bhai"             → False (a WORD is not noise)
      "ok 👍 koto lagbe?"   → False (mid-message emoji, still a question)
      "o👍k"                → False (an emoji in the middle is not an edge)
      "👍"                  → False (nothing left after stripping; this is
                                     the emoji-only branch's message anyway)
      "ok?"                 → False ('?' is not stripped)
      "okk"                 → False
      ""                    → False
    """
    if not text:
        return False
    return _normalise(text) in _ACKNOWLEDGEMENTS


# ============================================================
# Sticker detection
# ============================================================
def is_all_stickers(attachments: list) -> bool:
    """
    Return True if EVERY attachment in the list is a Messenger sticker.

    Keyed on `sticker_id` presence in the payload, and on nothing else.
    `type` is deliberately not checked — it is not stable across time:

      before ~1 Jun 2026   one attachment,  type "image"   + sticker_id
      transition window    TWO attachments, type "image" AND type "sticker",
                           both carrying sticker_id
      after 30 Aug 2026    one attachment,  type "sticker" + sticker_id

    Meta documents `sticker_id` as sticker-exclusive ("Applicable to
    attachment type: sticker. During the transition period, also present in
    attachment type: image when a sticker is sent"), so it is the one field
    that means the same thing in all three regimes. Any predicate written
    against `type` is wrong in at least one of them — which is exactly how
    this function broke: it required type == "image", Meta added the second
    "sticker" attachment, and every customer sticker fell through to the
    handoff branch and paused the thread for 7 days.

    ALL, NEVER ANY — this is the property that keeps the fix safe.
    A sticker sent alongside a genuine photo puts an attachment with no
    sticker_id in the same list. Quantified with all(), that list is False
    and takes the handoff branch, which is what we want: a real photo needs
    a human. Quantified with any(), the sticker's own id would vouch for the
    photo and the customer's image would be answered with "ধন্যবাদ" and
    never seen by a rep. The safety of this branch lives in the quantifier,
    not in the field.

    Presence test, not truthiness: sticker_id is documented as a Number, so
    `in payload` is the property being asserted.

    Returns False for empty lists (no attachments = not a sticker case).
    """
    if not attachments:
        return False

    for att in attachments:
        if not isinstance(att, dict):
            return False
        payload = att.get("payload") or {}
        if "sticker_id" not in payload:
            return False
    return True