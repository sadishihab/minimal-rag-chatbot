"""
Per-request utilities — request ID generation, timing helpers.
Keeps request-scoped concerns separate from server.py for readability.
"""
import uuid


def new_request_id() -> str:
    """
    Generate a short unique ID for a single HTTP request.
    Uses the first 8 hex chars of a UUID4 — ~4.3 billion combinations,
    unique enough for any realistic traffic volume.

    Example: 'a3f8c421'
    """
    return uuid.uuid4().hex[:8]