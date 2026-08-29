"""
Verify api/message_classifier.py handles all the emoji and sticker edge cases.

Run from project root:
    PYTHONPATH=. python tests/test_message_classifier.py

The prefix is required: nothing puts the repo root on sys.path when this
file is run directly, so the import below fails with ModuleNotFoundError.
tests/conftest.py does not help — conftest is a pytest mechanism and is not
read at all by a plain `python tests/...` run. Running it under pytest
hides that (pytest inserts its rootdir) but collects zero tests, because
the checks live under __main__ — so pytest reports success having run
nothing.

Exits 0 on success, 1 if any case fails. Prints per-case results.
"""
from api.message_classifier import is_emoji_only, is_all_stickers


def run_emoji_tests():
    cases = [
        # (input_text, expected_result, description)
        ("❤️",          True,  "single emoji with variation selector"),
        ("👍👍👍",       True,  "repeated thumbs-up"),
        ("😊 😊",        True,  "two emoji with whitespace"),
        ("👨‍👩‍👧‍👦",     True,  "family ZWJ sequence"),
        ("👍🏽",          True,  "thumbs-up with skin tone modifier"),
        ("🇧🇩",          True,  "Bangladesh flag (regional indicators)"),
        ("",            False, "empty string"),
        ("   ",         False, "whitespace only"),
        ("hello",       False, "ASCII letters"),
        ("hi 😊",       False, "letters + emoji (mixed)"),
        ("০১২",         False, "Bangla numerals (NOT emoji)"),
        ("hello 😊",    False, "English greeting + emoji"),
        ("আসসালামু",     False, "Bangla word"),
        ("123",         False, "ASCII digits"),
    ]

    print("=" * 70)
    print("EMOJI-ONLY TESTS")
    print("=" * 70)
    failed = 0
    for text, expected, description in cases:
        actual = is_emoji_only(text)
        status = "✅" if actual == expected else "🔴"
        if actual != expected:
            failed += 1
        print(f"  {status} {description}")
        print(f"      input={text!r}  expected={expected}  got={actual}")
    return failed


# ============================================================
# Sticker fixtures — built from Meta's documented payload, not from ours
# ============================================================
# The fixtures these replaced were invented. They used ONE attachment with
# no url:
#
#     [{"type": "image", "payload": {"sticker_id": 369239263222822}}]
#
# The sticker id is genuine — 369239263222822 is the Like sticker, straight
# out of Meta's field table — which is what made the fixture look sourced.
# Everything structural around it was derived from what the implementation
# believed, so the test could only ever restate is_all_stickers, never
# contradict it. Facebook actually sends TWO attachments today, and both
# carry a url.
#
# Meta, webhook-events/messages reference:
#   "During the 90-day transition period, both the `sticker` and `image`
#    attachment types are present in the payload. After August 30, 2026,
#    only the `sticker` attachment type will be sent."
#   "sticker_id | Number | ... Applicable to attachment type: `sticker`.
#    During the transition period (until August 30, 2026), also present in
#    attachment type: `image` when a sticker is sent."
#
# So there are three regimes, and is_all_stickers has to hold in all of
# them. Both live ones are covered below.

# Shape is from the docs; the specific CDN host is illustrative. What
# matters is that the field is PRESENT — its absence is half of what made
# the old fixture fiction.
STICKER_URL = "https://scontent.xx.fbcdn.net/v/t39.1997-6/39178562_1505197616216488_5411344281094586368_n.png"
LIKE_STICKER_ID = 369239263222822   # Meta's own example: the Like sticker

# Today (transition window): the legacy image attachment AND the new sticker
# attachment, both carrying sticker_id. This is the shape that broke the
# type == "image" check in production.
TRANSITION_STICKER = [
    {"type": "image", "payload": {"url": STICKER_URL, "sticker_id": LIKE_STICKER_ID}},
    {"type": "sticker", "payload": {"url": STICKER_URL, "sticker_id": LIKE_STICKER_ID}},
]

# After 30 Aug 2026: the image half is gone.
POST_TRANSITION_STICKER = [
    {"type": "sticker", "payload": {"url": STICKER_URL, "sticker_id": LIKE_STICKER_ID}},
]

# Before ~1 Jun 2026, and still worth pinning: type "image" alone was right
# once, and a predicate keyed on sticker_id keeps working on it.
LEGACY_STICKER = [
    {"type": "image", "payload": {"url": STICKER_URL, "sticker_id": LIKE_STICKER_ID}},
]

# A genuine photo: type "image", a url, and no sticker_id anywhere.
REAL_PHOTO = [
    {"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/v/t34.0-12/photo.jpg"}},
]


def run_sticker_tests():
    cases = [
        # (attachments, expected_result, description)
        (TRANSITION_STICKER, True,
         "sticker, transition shape (image + sticker, both sticker_id)"),
        (POST_TRANSITION_STICKER, True,
         "sticker, after 30 Aug 2026 (sticker only)"),
        (LEGACY_STICKER, True,
         "sticker, legacy shape (image + sticker_id)"),

        (REAL_PHOTO, False,
         "real photo (image with url, no sticker_id)"),

        # THE ONE THAT PINS all() AGAINST any().
        # A sticker and a genuine photo in the same message. Under any(),
        # the sticker's id vouches for the photo, the customer gets
        # "ধন্যবাদ", and no rep ever sees the image. Under all(), the photo
        # has no sticker_id, so the whole list is False and takes the
        # handoff branch — which is the correct outcome.
        (TRANSITION_STICKER + REAL_PHOTO, False,
         "sticker + real photo → NOT all stickers (any() would swallow the photo)"),
        (POST_TRANSITION_STICKER + REAL_PHOTO, False,
         "sticker (post-transition) + real photo → NOT all stickers"),

        ([{"type": "video", "payload": {"url": "https://cdn.example/v.mp4"}}], False,
         "video"),
        ([{"type": "audio", "payload": {"url": "https://cdn.example/a.mp4"}}], False,
         "audio (voice clip)"),
        ([{"type": "file", "payload": {"url": "https://cdn.example/f.pdf"}}], False,
         "file"),
        ([{"type": "fallback", "payload": {}}], False,
         "fallback (link share)"),

        ([], False, "empty list"),
        (["not-a-dict"], False, "malformed attachment (not a dict)"),
    ]

    print()
    print("=" * 70)
    print("STICKER TESTS")
    print("=" * 70)
    failed = 0
    for attachments, expected, description in cases:
        actual = is_all_stickers(attachments)
        status = "✅" if actual == expected else "🔴"
        if actual != expected:
            failed += 1
        print(f"  {status} {description}")
        print(f"      expected={expected}  got={actual}")
    return failed


if __name__ == "__main__":
    import sys
    emoji_failed = run_emoji_tests()
    sticker_failed = run_sticker_tests()
    total_failed = emoji_failed + sticker_failed
    print()
    print("=" * 70)
    if total_failed == 0:
        print(f"✅ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"🔴 {total_failed} test(s) FAILED")
        sys.exit(1)