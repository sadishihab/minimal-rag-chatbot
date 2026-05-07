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