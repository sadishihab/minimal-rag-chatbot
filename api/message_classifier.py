"""
Message classifier — detect special message shapes that need custom handling.

Currently used to distinguish:
  - Emoji-only text   → reply "ধন্যবাদ" (no pause)
  - Sticker-only attachments → reply "ধন্যবাদ" (no pause)
  - Anything else → handled by existing routing in messenger.py

Both cases are conceptually "customer expressed positive engagement without
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