"""
HTTP smoke test for the Minimal Limited chatbot API.

Usage (with server running on localhost:8000):
    python -m tests.test_api

Exits with non-zero status if any test fails — CI-ready.
"""
import sys
import requests


BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 30  # seconds — OpenAI calls can be slow


def _print_header(title: str) -> None:
    print(f"\n{'─' * 70}")
    print(title)
    print("─" * 70)


def test_health() -> bool:
    """GET /health should return {'status': 'ok'}."""
    _print_header("Test: GET /health")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"Response: {data}")
        if data.get("status") == "ok":
            print("✅ PASS")
            return True
        print(f"🔴 FAIL — expected status='ok', got {data}")
        return False
    except Exception as e:
        print(f"🔴 FAIL — {type(e).__name__}: {e}")
        return False


def test_chat(label: str, message: str, must_contain: list[str] | None = None) -> bool:
    """
    POST /chat with the given message.

    Args:
        label: Short description of what this test is checking
        message: The user query to send
        must_contain: Optional list of substrings the reply MUST contain.
                      Useful to verify key facts (phone numbers, package names).
    """
    _print_header(f"Test: POST /chat — {label}")
    print(f"Query: {message!r}")

    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json={"message": message},  # requests handles UTF-8 correctly
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        reply = response.json().get("reply", "")
        print(f"Reply: {reply}")

        # If the caller told us what the reply must contain, verify it
        if must_contain:
            missing = [s for s in must_contain if s not in reply]
            if missing:
                print(f"🔴 FAIL — reply missing expected substrings: {missing}")
                return False

        print("✅ PASS")
        return True

    except requests.exceptions.Timeout:
        print(f"🔴 FAIL — request timed out after {TIMEOUT}s")
        return False
    except requests.exceptions.ConnectionError:
        print(f"🔴 FAIL — could not connect to {BASE_URL} (is the server running?)")
        return False
    except Exception as e:
        print(f"🔴 FAIL — {type(e).__name__}: {e}")
        return False

def test_validation_error(label: str, payload: dict, expected_reason_substring: str | None = None) -> bool:
    """
    Send a malformed payload to /chat. Expect 422 with our clean error shape.
    """
    _print_header(f"Test: validation error — {label}")
    print(f"Payload: {payload}")

    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json=payload,
            timeout=TIMEOUT,
        )
        if response.status_code != 422:
            print(f"🔴 FAIL — expected 422, got {response.status_code}")
            return False

        body = response.json()
        print(f"Response body: {body}")

        if body.get("error") != "validation failed":
            print(f"🔴 FAIL — expected error='validation failed', got {body.get('error')!r}")
            return False

        if expected_reason_substring and expected_reason_substring not in body.get("reason", ""):
            print(f"🔴 FAIL — reason missing {expected_reason_substring!r}")
            return False

        print("✅ PASS")
        return True

    except Exception as e:
        print(f"🔴 FAIL — {type(e).__name__}: {e}")
        return False

def main() -> int:
    print("=" * 70)
    print("🧪 MINIMAL LIMITED API SMOKE TEST")
    print(f"Target: {BASE_URL}")
    print("=" * 70)

    results = []

    # 1. Health check
    results.append(test_health())

    # 2. Banglish query — basic RAG path
    results.append(test_chat(
        label="Banglish — general pricing",
        message="interior design er cost koto?",
        must_contain=["প্যাকেজ", "বেসিক"],
    ))

    # 3. Pure Bangla query — UTF-8 round-trip verification
    results.append(test_chat(
        label="Bangla — office location",
        message="আপনাদের অফিস কোথায়?",
        must_contain=["ঢাকা-১২২৯", "01775-760496"],
    ))

    # 4. English query — must still reply in Bangla
    results.append(test_chat(
        label="English — payment schedule",
        message="What is your payment schedule?",
        must_contain=["৩৫%", "৯৫%"],
    ))

    # 5. Out-of-scope — fallback test
    results.append(test_chat(
        label="Out-of-scope — should trigger fallback",
        message="Do you sell pet food?",
        must_contain=["দুঃখিত"],
    ))

    # 6. Edge case: empty message — Pydantic validation should allow,
    #    our sanitizer should reject with the empty-input fallback
    results.append(test_chat(
        label="Edge case — empty message",
        message="",
        must_contain=["দুঃখিত"],
    ))

    # 7. Oversized payload — middleware should reject with 413 before routing
    _print_header("Test: POST /chat — oversized payload (should be rejected)")
    huge_message = "x" * 50000  # 50KB of payload
    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json={"message": huge_message},
            timeout=TIMEOUT,
        )
        if response.status_code == 413:
            print(f"Response status: 413 (Payload Too Large)")
            print("✅ PASS")
            results.append(True)
        else:
            print(f"🔴 FAIL — expected 413, got {response.status_code}")
            print(f"Body: {response.text[:200]}")
            results.append(False)
    except Exception as e:
        print(f"🔴 FAIL — {type(e).__name__}: {e}")
        results.append(False)

    # 8. Missing 'message' field
    results.append(test_validation_error(
        label="missing 'message' field",
        payload={"wrong_field": "hi"},
        expected_reason_substring="required",
    ))

    # 9. 'message' is not a string
    results.append(test_validation_error(
        label="'message' is not a string",
        payload={"message": 12345},
        expected_reason_substring="string",
    ))

    # 10. 'message' exceeds max_length (but body is under middleware threshold)
    results.append(test_validation_error(
        label="'message' exceeds max_length",
        payload={"message": "x" * 2000},
        expected_reason_substring="at most",
    ))

    # Summary
    total = len(results)
    passed = sum(results)
    failed = total - passed

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{total} passed")
    if failed:
        print(f"🔴 {failed} test(s) failed.")
        return 1
    print("✅ All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())