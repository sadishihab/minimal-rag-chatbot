"""
Central logging configuration for the Minimal Limited RAG bot.
Import and call setup_logging() once at app startup.
Every other module just does `logging.getLogger(__name__)` and uses it.
"""
import logging
import sys
from pathlib import Path

from config import BASE_DIR


# Log directory + file
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "minimal_rag.log"


def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logger to write to both console AND a file.
    Call this ONCE at app startup.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
               Use DEBUG while developing, INFO in production.
    """
    # Make sure logs/ directory exists
    LOG_DIR.mkdir(exist_ok=True)

    # Standard format: timestamp | level | module | message
    log_format = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Handler 1: console (stdout) — for developer visibility
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Handler 2: file — for long-term audit trail
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Configure root logger — all child loggers inherit these handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any default handlers (avoids duplicate log lines)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers — we don't need OpenAI's internal debug spam
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # Silence uvicorn's access log — our middleware produces structured equivalents
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.info(f"Logging configured — writing to {LOG_FILE}")


if __name__ == "__main__":
    # Self-test: set up logging and emit one message at each level
    setup_logging(level="DEBUG")

    log = logging.getLogger(__name__)
    log.debug("This is a DEBUG message (development detail)")
    log.info("This is an INFO message (normal operation)")
    log.warning("This is a WARNING message (something odd, but recoverable)")
    log.error("This is an ERROR message (something broke)")

    print("\n✅ Check logs/minimal_rag.log — all 4 messages should be there.")