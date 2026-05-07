"""
Input sanitization for user messages before they reach the RAG pipeline.
Catches edge cases that aren't errors but shouldn't hit OpenAI as-is.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from config import MAX_INPUT_LENGTH, MIN_INPUT_LENGTH


log = logging.getLogger(__name__)


@dataclass
class SanitizationResult:
    """
    Result of sanitizing a user message.
    - If .is_valid is False, use .rejection_reason to decide fallback
    - If .is_valid is True, use .cleaned_text for downstream processing
    """
    is_valid: bool
    cleaned_text: str
    rejection_reason: Optional[str] = None
    was_truncated: bool = False


def sanitize_input(raw_text: str) -> SanitizationResult:
    """
    Clean and validate a user message.
    Returns a SanitizationResult — never raises.
    """
    # Handle None or non-string input defensively
    if raw_text is None or not isinstance(raw_text, str):
        return SanitizationResult(
            is_valid=False,
            cleaned_text="",
            rejection_reason="not_a_string",
        )

    # Strip leading/trailing whitespace
    cleaned = raw_text.strip()

    # Check: too short after stripping
    if len(cleaned) < MIN_INPUT_LENGTH:
        return SanitizationResult(
            is_valid=False,
            cleaned_text=cleaned,
            rejection_reason="too_short",
        )

    # Check: too long — truncate but keep valid
    was_truncated = False
    if len(cleaned) > MAX_INPUT_LENGTH:
        log.warning(
            f"Input truncated from {len(cleaned)} to {MAX_INPUT_LENGTH} chars"
        )
        cleaned = cleaned[:MAX_INPUT_LENGTH]
        was_truncated = True

    return SanitizationResult(
        is_valid=True,
        cleaned_text=cleaned,
        was_truncated=was_truncated,
    )


if __name__ == "__main__":
    from logger import setup_logging
    setup_logging(level="INFO")

    print("🧪 SANITIZER SELF-TEST\n")

    test_cases = [
        # (input, expected_valid, expected_reason)
        ("interior design er cost koto?", True, None),
        ("", False, "too_short"),
        ("   ", False, "too_short"),
        ("?", False, "too_short"),
        ("hi", True, None),  # exactly at boundary
        (None, False, "not_a_string"),
        (12345, False, "not_a_string"),  # not a string at all
        ("a" * 1500, True, None),  # will be truncated to 1000
    ]

    for i, (input_val, expected_valid, expected_reason) in enumerate(test_cases, start=1):
        result = sanitize_input(input_val)
        display_input = repr(input_val) if not isinstance(input_val, str) or len(input_val) < 50 else f"'{input_val[:30]}...' ({len(input_val)} chars)"

        valid_ok = result.is_valid == expected_valid
        reason_ok = result.rejection_reason == expected_reason
        passed = valid_ok and reason_ok
        status = "✅" if passed else "🔴"

        print(f"{status} Test {i}: input={display_input}")
        print(f"   is_valid={result.is_valid}, reason={result.rejection_reason}, truncated={result.was_truncated}")
        if result.was_truncated:
            print(f"   cleaned length: {len(result.cleaned_text)} chars")
        print()