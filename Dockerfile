# =============================================================================
# Dockerfile — Minimal Limited Bangla RAG Chatbot
#
# Multi-stage build:
#   1. builder: installs Python deps into a virtualenv (heavier, has gcc etc.)
#   2. runtime: copies only what's needed, runs as non-root user
#
# Build:    docker build -t minimal-rag:local .
# Run:      docker run --rm -p 8000:8000 --env-file .env minimal-rag:local
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder — install Python dependencies
# -----------------------------------------------------------------------------
FROM python:3.13-slim AS builder

# System deps needed for building some Python packages (faiss-cpu compile etc.).
# We install in builder only — runtime image won't carry these.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtualenv. This isolates our deps and makes copying to runtime trivial.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies. Copy requirements.txt FIRST so this layer is cacheable —
# rebuilds skip pip install unless requirements.txt actually changed.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: runtime — minimal image with just the venv and app code
# -----------------------------------------------------------------------------
FROM python:3.13-slim

# Create non-root user. UID 1000 is the conventional first non-root user.
RUN groupadd -r appuser --gid 1000 && \
    useradd -r -g appuser --uid 1000 --create-home --shell /bin/sh appuser

# Copy the virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# App lives in /app, owned by appuser.
WORKDIR /app
RUN chown appuser:appuser /app

# Copy app code. .dockerignore controls what's actually copied.
# --chown ensures appuser can read+write everything.
COPY --chown=appuser:appuser . .

# Make entrypoint executable.
RUN chmod +x /app/entrypoint.sh

# Create directories that may not exist in source but the app expects.
# vector_store: indexer writes here on first start.
# logs: app writes here at runtime.
RUN mkdir -p /app/vector_store /app/logs && \
    chown -R appuser:appuser /app/vector_store /app/logs

# Drop privileges — everything from here runs as appuser.
USER appuser

# Document which port the app listens on (informational; doesn't actually publish).
EXPOSE 8000

# Healthcheck: hit /health every 30s, container marked unhealthy after 3 failures.
# Note: requires `requests` or curl. We use python since it's already in image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# Use the entrypoint script to handle conditional indexer + uvicorn launch.
ENTRYPOINT ["/app/entrypoint.sh"]
