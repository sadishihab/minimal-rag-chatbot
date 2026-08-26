"""
Central configuration for Minimal Limited RAG chatbot.
All paths, model names, and settings live here.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================================================
# PROJECT PATHS
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
VECTOR_STORE_DIR = BASE_DIR / "vector_store"

KNOWLEDGE_BASE_PATH = DATA_DIR / "knowledge_base.json"
FAISS_INDEX_PATH = VECTOR_STORE_DIR / "faiss.index"
METADATA_PATH = VECTOR_STORE_DIR / "metadata.json"

# ============================================================
# OPENAI SETTINGS
# ============================================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Facebook Messenger credentials (Phase 6)
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
FACEBOOK_APP_ID = int(os.getenv("FACEBOOK_APP_ID", "0"))

# Embedding model — used to convert text to vectors for search
EMBEDDING_MODEL = "text-embedding-3-small"  # cheap & fast, 1536 dimensions
EMBEDDING_DIMENSIONS = 1536

# Chat model — used to generate the final reply
CHAT_MODEL = "gpt-4o-mini"  # cheap, fast, handles Bangla well
CHAT_TEMPERATURE = 0.3       # lower = more consistent answers
CHAT_MAX_TOKENS = 500        # keep replies short

# ============================================================
# RETRIEVAL SETTINGS
# ============================================================
TOP_K = 4  # How many KB entries to retrieve per query
SIMILARITY_THRESHOLD = 0.3  # Minimum similarity score (0-1) to consider a match

# ============================================================
# INPUT SANITIZATION
# ============================================================
MAX_INPUT_LENGTH = 1000   # Truncate user messages longer than this (chars)
MIN_INPUT_LENGTH = 2      # Reject messages shorter than this after strip

# ============================================================
# HTTP LAYER LIMITS
# ============================================================
MAX_REQUEST_BODY_BYTES = 10 * 1024   # 10 KB — plenty for chat messages + JSON overhead

# ============================================================
# FASTAPI / MESSENGER SETTINGS (used later in Phase 5-6)
# ============================================================
API_HOST = "0.0.0.0"
API_PORT = 8000

# ============================================================
# ACTIVE HOURS (Messenger webhook gate)
# ============================================================
# The bot covers the overnight shift, when no rep is watching Page Inbox.
# Outside this window api/messenger.py drops every webhook event without
# sending, reading pause state, or calling the RAG pipeline.
#
# Start is inclusive, end is exclusive, hour granularity. The window may
# wrap midnight (the 23 -> 9 default spans two calendar days).
# END accepts 24 ("end of day") so 0 -> 24 expresses always-active.
#
# Timezone is pinned in api/active_hours.py and is deliberately NOT an
# env var — the business is in Dhaka wherever the container runs.
BOT_TIMEZONE = "Asia/Dhaka"


def _hour_from_env(name: str, default: str) -> int:
    """Parse an hour-valued env var, failing loudly on a non-integer."""
    raw = os.getenv(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


BOT_ACTIVE_START_HOUR = _hour_from_env("BOT_ACTIVE_START_HOUR", "23")
BOT_ACTIVE_END_HOUR = _hour_from_env("BOT_ACTIVE_END_HOUR", "9")


def _day_list_from_env(name: str, default: str = "") -> tuple[str, ...]:
    """
    Parse a comma-separated weekday list into normalised lowercase tokens.

    Splitting and normalisation only — which names are legal is decided by
    api.active_hours.validate_days, next to the logic that consumes them,
    exactly as the hour range checks live next to the window logic.

    Whitespace around each name is stripped and empty tokens are dropped,
    so a trailing comma is not an error.
    """
    raw = os.getenv(name, default)
    return tuple(token.strip().lower() for token in raw.split(",") if token.strip())


# Whole days on which the bot is active regardless of the hour window.
# Friday is the client's holiday: no rep is at Page Inbox all day, so the
# bot covers Thursday 23:00 through Saturday 09:00 as one continuous
# stretch. Empty by default — unset means the window alone decides.
BOT_ALWAYS_ACTIVE_DAYS = _day_list_from_env("BOT_ALWAYS_ACTIVE_DAYS")

# Range, degenerate-window and day-name checks live in api/active_hours,
# next to the logic that consumes them, and run at that module's import.

# ============================================================
# VALIDATION
# ============================================================
def validate_config():
    """Check that all critical settings are present."""
    errors = []

    if not OPENAI_API_KEY:
        errors.append("❌ OPENAI_API_KEY is missing. Check your .env file.")
    elif not OPENAI_API_KEY.startswith("sk-"):
        errors.append("❌ OPENAI_API_KEY doesn't look valid. Should start with 'sk-'.")

    if not KNOWLEDGE_BASE_PATH.exists():
        errors.append(f"❌ Knowledge base not found at: {KNOWLEDGE_BASE_PATH}")

    if not VECTOR_STORE_DIR.exists():
        errors.append(f"❌ Vector store directory not found at: {VECTOR_STORE_DIR}")

    if errors:
        print("\n".join(errors))
        return False

    print("✅ Config looks good!")
    print(f"   - OpenAI key loaded: sk-...{OPENAI_API_KEY[-4:]}")
    print(f"   - Knowledge base: {KNOWLEDGE_BASE_PATH}")
    print(f"   - Vector store:   {VECTOR_STORE_DIR}")
    return True


if __name__ == "__main__":
    validate_config()