"""
Post-processing for GPT replies before sending to Messenger.
Strips markdown syntax that Messenger does not render.
"""
import re


# Regex patterns — compiled once, reused forever
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^\*\n]+?)\*(?!\*)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def strip_markdown(text: str) -> str:
    """
    Remove markdown syntax that Messenger cannot render.

    Transformations:
      [label](url)  →  url           (drop label, keep raw URL)
      **text**      →  text          (bold markers removed)
      *text*        →  text          (italic markers removed)
      `text`        →  text          (code markers removed)
    """
    if not text:
        return text

    # Order matters: strip links FIRST (before bold/italic),
    # because link labels can contain * or ** characters.
    text = _MARKDOWN_LINK.sub(r"\2", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)

    return text


if __name__ == "__main__":
    # Quick self-test with examples that mirror what GPT actually produces
    test_cases = [
        # (input, expected_output)
        (
            "বিস্তারিত জানার জন্য [এখানে ক্লিক করুন](https://facebook.com/abc)।",
            "বিস্তারিত জানার জন্য https://facebook.com/abc।",
        ),
        (
            "আমাদের **৪টি** প্যাকেজ আছে।",
            "আমাদের ৪টি প্যাকেজ আছে।",
        ),
        (
            "দাম *আনুমানিক* ৯ লাখ টাকা।",
            "দাম আনুমানিক ৯ লাখ টাকা।",
        ),
        (
            "ফোন: `01775-760496`",
            "ফোন: 01775-760496",
        ),
        (
            "Plain text with no markdown stays the same.",
            "Plain text with no markdown stays the same.",
        ),
        (
            # Combined case — all four at once
            "দেখুন [এখানে](https://x.com) — **দাম** *২০০০* `টাকা`।",
            "দেখুন https://x.com — দাম ২০০০ টাকা।",
        ),
    ]

    print("🧪 FORMATTER SELF-TEST\n")
    all_passed = True
    for i, (input_text, expected) in enumerate(test_cases, start=1):
        result = strip_markdown(input_text)
        passed = result == expected
        status = "✅" if passed else "🔴"
        print(f"{status} Test {i}")
        print(f"   Input:    {input_text}")
        print(f"   Expected: {expected}")
        print(f"   Got:      {result}")
        if not passed:
            all_passed = False
        print()

    print("=" * 60)
    print("✅ All tests passed!" if all_passed else "🔴 Some tests FAILED.")