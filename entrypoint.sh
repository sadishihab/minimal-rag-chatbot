#!/bin/sh
# =============================================================================
# entrypoint.sh — container startup script
#
# Runs whenever the container starts. Two phases:
#   1. Ensure FAISS index exists (build it if missing — needs OPENAI_API_KEY)
#   2. Start uvicorn server
#
# Why a script (not just CMD): we need conditional logic ("if index missing,
# build it") which is hard to express cleanly in a Dockerfile CMD.
#
# Why /bin/sh (not bash): slim Python images don't include bash. /bin/sh is
# always available and sufficient for our needs.
# =============================================================================

set -e  # exit immediately on any command failure

INDEX_FILE="/app/vector_store/faiss.index"
METADATA_FILE="/app/vector_store/metadata.json"

echo "[entrypoint] Container starting..."

# --- Phase 1: Build FAISS index if missing ---
if [ ! -f "$INDEX_FILE" ] || [ ! -f "$METADATA_FILE" ]; then
    echo "[entrypoint] FAISS index not found, running indexer..."
    echo "[entrypoint] (this requires OPENAI_API_KEY; ~30 seconds, ~\$0.001)"
    python -m ingestion.indexer
    echo "[entrypoint] ✅ Indexer complete"
else
    echo "[entrypoint] ✅ FAISS index found, skipping build"
fi

# --- Phase 2: Start uvicorn ---
echo "[entrypoint] Starting uvicorn on 0.0.0.0:8000..."
exec uvicorn api.server:app --host 0.0.0.0 --port 8000

# Note: 'exec' replaces the shell with uvicorn so signals (SIGTERM, SIGINT)
# go directly to uvicorn for clean shutdown. Without 'exec', Docker stop
# would kill the shell but uvicorn wouldn't get the signal.
