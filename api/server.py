"""
FastAPI application for the Minimal Limited RAG chatbot.
Exposes /chat (for queries) and /health (for monitoring).

This is the HTTP layer — it wraps the Generator in a web-accessible interface.
Facebook Messenger integration lives in a separate module (Phase 6).
"""
import logging
import time

from fastapi.exceptions import RequestValidationError
from logger import setup_logging
from generation.generator import Generator
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from api.request_context import new_request_id
from api.messenger import router as messenger_router

from config import (
    MAX_INPUT_LENGTH,
    MAX_REQUEST_BODY_BYTES,
)



# Configure logging BEFORE importing anything that might log at import time
setup_logging(level="INFO")
log = logging.getLogger(__name__)

# ============================================================
# UTF-8 JSON RESPONSE CLASS
# ============================================================
# FastAPI's default JSONResponse omits "charset=utf-8" from its Content-Type
# header. This is spec-compliant but trips up some HTTP clients (e.g. PowerShell)
# into decoding our Bangla responses as Latin-1. We fix it globally by setting
# the correct media_type on every JSON response.
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(
    title="Minimal Limited RAG Chatbot",
    description="Customer service bot for Minimal Limited, replies in formal Bangla.",
    version="0.1.0",
    default_response_class=UTF8JSONResponse,
)

# Register Messenger webhook routes (Phase 6)
app.include_router(messenger_router)

# ============================================================
# VALIDATION ERROR HANDLER
# ============================================================
# Replace FastAPI's verbose default 422 response with a clean, consistent shape.
# Full technical details are logged server-side for debugging.
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Log the full details for developer visibility
    log.warning(
        f"Validation error on {request.method} {request.url.path}: "
        f"{exc.errors()}"
    )

    # Extract a short, human-readable summary of what went wrong.
    # We use the first error's 'msg' field — usually the most actionable part.
    errors = exc.errors()
    reason = errors[0].get("msg", "invalid request") if errors else "invalid request"

    return JSONResponse(
        status_code=422,
        content={"error": "validation failed", "reason": reason},
    )

# ============================================================
# UNHANDLED EXCEPTION HANDLER (fallback 500)
# ============================================================
# Catches any exception that escapes the RAG pipeline's own error handling.
# The generator has its own internal handlers for OpenAI errors — this is
# the last-resort net for truly unexpected failures (import errors, FAISS
# corruption, programming bugs, etc.).
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception(
        f"Unhandled exception on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}"
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal server error"},
    )

# ============================================================
# BODY-SIZE MIDDLEWARE
# ============================================================
# Reject oversized request bodies BEFORE parsing them.
# This protects against abuse and runaway payloads.
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                log.warning(
                    f"Rejected oversized request: "
                    f"{content_length} bytes > {MAX_REQUEST_BODY_BYTES} byte limit"
                )
                return JSONResponse(
                    status_code=413,  # "Payload Too Large"
                    content={"error": "request body too large"},
                )
        except ValueError:
            # Header present but not a valid integer — let it through,
            # downstream parsers will reject it with a clearer error
            pass

    return await call_next(request)

# ============================================================
# REQUEST LOGGING MIDDLEWARE
# ============================================================
# Registered LAST so it runs FIRST (ASGI middleware order is reversed).
# Wraps the entire request lifecycle to capture timing and response status.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = new_request_id()
    start_time = time.perf_counter()

    log.info(
        f"[{request_id}] → {request.method} {request.url.path} "
        f"client={request.client.host if request.client else 'unknown'}"
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        # If an exception bubbles all the way up to us, our @app.exception_handler
        # has already sent a 500 response to the client. Just log the bookend
        # at ERROR level to keep the [request_id] correlation clean.
        # We do NOT re-raise: that would cause starlette to log the traceback again.
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.error(
            f"[{request_id}] ← {request.method} {request.url.path} "
            f"500 in {duration_ms:.0f}ms (caught: {type(exc).__name__})"
        )
        # Return a response consistent with our 500 handler's shape,
        # in case the framework's handler somehow didn't run.
        return JSONResponse(
            status_code=500,
            content={"error": "internal server error"},
        )

    duration_ms = (time.perf_counter() - start_time) * 1000
    status = response.status_code

    # Choose log level based on status code
    if status >= 500:
        log_method = log.error
    elif status >= 400:
        log_method = log.warning
    else:
        log_method = log.info

    log_method(
        f"[{request_id}] ← {request.method} {request.url.path} "
        f"{status} in {duration_ms:.0f}ms"
    )

    return response

# ============================================================
# STARTUP: Pre-load the Generator once, reuse for every request
# ============================================================
generator: Generator | None = None


@app.on_event("startup")
def startup_event():
    """
    Load the Generator (which loads FAISS index + OpenAI client) ONCE
    when the server starts, not on every request.
    Stored in app.state so other modules (like api/messenger.py) can access it.
    """
    global generator
    log.info("Starting Minimal Limited RAG API...")
    generator = Generator()
    app.state.generator = generator
    log.info("API ready to serve requests.")


# ============================================================
# REQUEST / RESPONSE SCHEMAS
# ============================================================

class ChatRequest(BaseModel):
    """Incoming chat message from a client."""
    message: str = Field(
        ...,
        max_length=MAX_INPUT_LENGTH,
        description="The user's message to the chatbot (max 1000 characters).",
    )


class ChatResponse(BaseModel):
    """Outgoing reply from the chatbot."""
    reply: str = Field(
        ...,
        description="The chatbot's reply in formal Bangla.",
    )


# ============================================================
# ENDPOINTS
# ============================================================
@app.get("/health")
def health():
    """Simple health check — used by monitoring and Messenger webhook verification."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """
    Accept a user message, run it through the RAG pipeline,
    return a formal Bangla reply.
    """
    log.info(f"POST /chat — message length: {len(request.message)}")
    reply = generator.generate(request.message)
    return ChatResponse(reply=reply)
