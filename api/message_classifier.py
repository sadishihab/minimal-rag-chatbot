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
# must reach the pipeline. Leading punctuation is likewise left alone.
_TRAILING_PUNCTUATION = ".!।॥, \t\n"


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
    normalised = normalised.rstrip(_TRAILING_PUNCTUATION)
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
    '. ! । ॥ ,', Unicode composition, and zero-width characters are all
    normalised away first. A question mark is not.

    Examples:
      "Ok"              → True
      "ok."             → True  (collapses onto "ok")
      " THANKS "        → True
      "thank  you"      → True
      "ঠিক আছে।"        → True
      "ok koto lagbe?"  → False (a question that starts with an ack)
      "thanks a lot"    → False (not a whole-message match)
      "ok?"             → False ('?' is not stripped)
      "okk"             → False
      ""                → False
    """
    if not text:
        return False
    return _normalise(text) in _ACKNOWLEDGEMENTS


# ============================================================
# Sticker detection
# ============================================================
def is_all_stickers(attachments: list) -> bool:
    """
    Return True if every attachment in the list is a Messenger sticker.

    FB sends stickers as attachments with:
      - type == "image"
      - payload contains "sticker_id"

    The "type": "sticker" naming is a common gotcha — FB actually uses
    "image" with a sticker_id in the payload.

    Returns False for empty lists (no attachments = not a sticker case).
    """
    if not attachments:
        return False

    for att in attachments:
        if not isinstance(att, dict):
            return False
        att_type = att.get("type")
        payload = att.get("payload") or {}
        if att_type != "image":
            return False
        if "sticker_id" not in payload:
            return False
    return True