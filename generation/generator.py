"""
Generator: the full RAG pipeline in one place.
Takes a user message, retrieves context, calls GPT, returns a Bangla reply.
Handles failures gracefully with Bangla fallback messages.
"""
import logging

from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    APIError,
)

from config import (
    OPENAI_API_KEY,
    CHAT_MODEL,
    CHAT_TEMPERATURE,
    CHAT_MAX_TOKENS,
)
from retrieval.retriever import Retriever
from generation.prompt_builder import build_messages
from generation.formatter import strip_markdown
from generation.sanitizer import sanitize_input
from generation.phone_detector import contains_phone_number, PHONE_ACKNOWLEDGMENT


log = logging.getLogger(__name__)


# ============================================================
# FALLBACK MESSAGES — what the customer sees when something breaks
# ============================================================
FALLBACK_TRANSIENT = (
    "দুঃখিত, এই মুহূর্তে আমাদের সিস্টেম একটু ব্যস্ত। "
    "অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন।"
)

FALLBACK_CONNECTION = (
    "দুঃখিত, এই মুহূর্তে সেবা সাময়িকভাবে বন্ধ রয়েছে। "
    "সরাসরি যোগাযোগের জন্য কল করুন: 01775-760496।"
)

FALLBACK_GENERIC = (
    "দুঃখিত, আপনার বার্তাটি প্রক্রিয়া করতে সমস্যা হচ্ছে। "
    "সরাসরি যোগাযোগের জন্য কল করুন: 01775-760496।"
)

FALLBACK_EMPTY_INPUT = (
    "দুঃখিত, আপনার বার্তাটি বুঝতে পারিনি। অনুগ্রহ করে আবার লিখুন।"
)

FALLBACK_INVALID_INPUT = (
    "দুঃখিত, আপনার বার্তাটি প্রক্রিয়া করা যাচ্ছে না। "
    "অনুগ্রহ করে আপনার প্রশ্নটি স্পষ্টভাবে লিখুন।"
)


class Generator:
    """
    End-to-end reply generator.
    Instantiate ONCE at app startup, then call .generate() for every message.
    """

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.retriever = Retriever()
        log.info(f"Generator initialized (model={CHAT_MODEL})")

    def generate(self, user_message: str) -> str:
        """
        Full RAG pipeline with graceful error handling.
        Always returns a user-facing string — never raises to caller.
        """
        # Step 0: Sanitize input
        sanitation = sanitize_input(user_message)
        if not sanitation.is_valid:
            log.warning(
                f"Input rejected: reason={sanitation.rejection_reason} "
                f"raw={user_message!r}"
            )
            if sanitation.rejection_reason == "too_short":
                return FALLBACK_EMPTY_INPUT
            return FALLBACK_INVALID_INPUT

        user_message = sanitation.cleaned_text
        if sanitation.was_truncated:
            log.info(f"Query received (truncated): {user_message!r}")
        else:
            log.info(f"Query received: {user_message!r}")

        # Step 0.5: Phone number bypass
        # If the customer shared a phone number, skip RAG entirely
        # and return the canned acknowledgment + handoff message.
        if contains_phone_number(user_message):
            log.info("Phone number detected — bypassing RAG, sending canned ack")
            return PHONE_ACKNOWLEDGMENT

        # Step 1: Retrieve
        try:
            results = self.retriever.search(user_message)
            log.info(
                f"Retrieved {len(results)} results "
                f"(top score: {results[0].score:.3f})" if results
                else "Retrieved 0 results (no KB match above threshold)"
            )
        except Exception as e:
            log.exception(f"Retrieval failed: {e}")
            return FALLBACK_GENERIC

        # Step 2: Build prompt
        messages = build_messages(user_message, results)

        # Step 3: Call OpenAI with specific error handling
        try:
            response = self.client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=CHAT_TEMPERATURE,
                max_tokens=CHAT_MAX_TOKENS,
            )
        except RateLimitError as e:
            log.warning(f"OpenAI rate limit hit: {e}")
            return FALLBACK_TRANSIENT
        except APITimeoutError as e:
            log.warning(f"OpenAI timeout: {e}")
            return FALLBACK_TRANSIENT
        except APIConnectionError as e:
            log.error(f"OpenAI connection error: {e}")
            return FALLBACK_CONNECTION
        except AuthenticationError as e:
            # This is a developer problem, not a user problem — log loudly
            log.critical(f"OpenAI authentication failed — check API key! {e}")
            return FALLBACK_GENERIC
        except BadRequestError as e:
            log.error(f"OpenAI bad request: {e}")
            return FALLBACK_GENERIC
        except APIError as e:
            log.error(f"OpenAI API error: {e}")
            return FALLBACK_GENERIC
        except Exception as e:
            # Last-resort safety net for any unexpected exception
            log.exception(f"Unexpected error during OpenAI call: {e}")
            return FALLBACK_GENERIC

        # Step 4: Extract text
        reply = response.choices[0].message.content.strip()

        # Step 5: Strip any markdown that slipped through
        reply = strip_markdown(reply)

        log.info(f"Reply generated ({len(reply)} chars): {reply!r}")
        return reply


if __name__ == "__main__":
    # Set up logging for this standalone run
    from logger import setup_logging
    setup_logging(level="INFO")

    print("=" * 60)
    print("🧪 GENERATOR STRESS TEST")
    print("=" * 60)

    generator = Generator()

    test_queries = [
        "interior design er cost koto?",
        "আপনাদের অফিস কোথায়?",
        "What is your payment schedule?",
        "kitchen cabinet er dam koto?",
        "Do you sell pet food?",
        "",                               # empty input — should trigger guard
        "   ",                            # whitespace-only — same
    ]

    for i, query in enumerate(test_queries, start=1):
        print(f"\n{'─' * 60}")
        print(f"Test {i}: {query!r}")
        print("─" * 60)
        reply = generator.generate(query)
        print(reply)

    print(f"\n{'=' * 60}")
    print("✅ All tests complete. Check logs/minimal_rag.log for details.")