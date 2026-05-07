"""
Verify api/message_classifier.py handles all the emoji and sticker edge cases.

Run from project root:
    python tests/test_message_classifier.py

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


def run_sticker_tests():
    cases = [
        # (attachments, expected_result, description)
        ([{"type": "image", "payload": {"sticker_id": 369239263222822}}], True,
         "single sticker"),
        ([{"type": "image", "payload": {"sticker_id": 1}},
          {"type": "image", "payload": {"sticker_id": 2}}], True,
         "two stickers"),
        ([{"type": "image", "payload": {}}], False,
         "image without sticker_id (real photo)"),
        ([{"type": "image", "payload": {"url": "https://..."}}], False,
         "image with URL only (real photo)"),
        ([{"type": "image", "payload": {"sticker_id": 1}},
          {"type": "image", "payload": {"url": "https://..."}}], False,
         "sticker + photo mixed"),
        ([{"type": "video", "payload": {"url": "..."}}], False,
         "video"),
        ([{"type": "audio", "payload": {"url": "..."}}], False,
         "audio"),
        ([{"type": "file", "payload": {"url": "..."}}], False,
         "file"),
        ([], False, "empty list"),
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