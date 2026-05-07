"""
Phone number detection for incoming customer messages.
When a customer shares a phone number, we bypass the RAG pipeline entirely
and respond with a canned acknowledgment + handoff message.
"""
import re

# Bangladeshi mobile number patterns:
#   01775760496        (11 digits, starts with 01)
#   +8801775760496     (with country code +880)
#   8801775760496      (country code, no plus)
# Mobile prefixes after 01: 013-019 (operators Grameenphone, Robi, Banglalink, etc.)
#
# We strip all dashes/spaces from the input first, then match.
# This handles common customer formats like "01775-760496" or "01775 760496".
_BD_PHONE_PATTERN = re.compile(
    r'(?:\+?880)?01[3-9]\d{8}',
    re.IGNORECASE,
)

# Canned response when a phone number is detected.
# Per business rules: thank the customer + assure callback.
PHONE_ACKNOWLEDGMENT = (
    "মোবাইল নম্বর শেয়ার করার জন্য ধন্যবাদ। "
    "আমাদের একজন প্রতিনিধি আপনাকে কল করে বিস্তারিত জানাবেন।"
)


def contains_phone_number(text: str) -> bool:
    """
    Check if the text contains a Bangladeshi mobile phone number.
    Strips dashes/spaces first to handle formatted numbers like '01775-760496'.

    Returns True if at least one phone number is found.
    """
    if not text or not isinstance(text, str):
        return False
    # Normalize: remove dashes, spaces, and parentheses to handle formatting variations
    normalized = re.sub(r'[-\s()]', '', text)
    return bool(_BD_PHONE_PATTERN.search(normalized))


if __name__ == "__main__":
    print("🧪 PHONE DETECTOR SELF-TEST\n")

    test_cases = [
        # (input, expected_match)
        ("01775760496", True),
        ("01775-760496", True),
        ("01775 760496", True),
        ("+8801775760496", True),
        ("8801775760496", True),
        ("My number is 01775760496", True),
        ("Call me at +88 01775-760496 anytime", True),
        ("01777411014 - my whatsapp", True),
        ("interior design er cost koto?", False),
        ("আমার বাসা ১২০০ সাইজের", False),
        ("", False),
        ("call 999 emergency", False),  # not a BD mobile
        ("0123456789", False),  # 02 prefix, not mobile
        ("01225456789", False),  # 012 — invalid mobile prefix
    ]

    passed_count = 0
    for i, (text, expected) in enumerate(test_cases, start=1):
        result = contains_phone_number(text)
        ok = result == expected
        status = "✅" if ok else "🔴"
        print(f"{status} Test {i}: input={text!r}")
        print(f"   expected={expected}, got={result}")
        if ok:
            passed_count += 1

    print(f"\n{passed_count}/{len(test_cases)} tests passed")