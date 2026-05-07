"""
Interactive CLI chat for exploratory testing.

Usage:
    python -m tests.chat_cli

Talks directly to the Generator (no HTTP server needed).
Type a message and press Enter to get a reply.

Commands (type at the prompt):
    /quit  or  /q       — exit
    /help               — show commands
    /last               — repeat the last reply (useful for re-reading long answers)
    /stats              — show session stats (total queries, avg response time)
    /retrieval          — toggle showing retrieval debug info (scores, matched intents)
"""
import sys
import time
from typing import Optional

from logger import setup_logging
from generation.generator import Generator
from retrieval.retriever import Retriever


# ANSI color codes for readability
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
GRAY = "\033[90m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_banner() -> None:
    print(f"{BOLD}{CYAN}")
    print("=" * 70)
    print("  Minimal Limited Chatbot — Interactive CLI")
    print("  Type /help for commands, /quit to exit")
    print("=" * 70)
    print(RESET)


def print_help() -> None:
    print(f"{GRAY}")
    print("Commands:")
    print("  /quit  or  /q    — exit the chat")
    print("  /help            — show this help")
    print("  /last            — repeat the last reply (re-read long answers)")
    print("  /stats           — session statistics")
    print("  /retrieval       — toggle retrieval debug (show KB matches & scores)")
    print(RESET)


def format_retrieval_debug(retriever: Retriever, query: str) -> str:
    """Show which KB entries matched — useful for diagnosing why the bot answered a certain way."""
    results = retriever.search(query)
    if not results:
        return f"{GRAY}   [retrieval: 0 results above threshold]{RESET}"

    lines = [f"{GRAY}   [retrieval debug — top {len(results)} matches]"]
    for i, r in enumerate(results, start=1):
        # Truncate question to keep the display compact
        q_preview = r.question[:60] + ("..." if len(r.question) > 60 else "")
        lines.append(f"   {i}. score={r.score:.3f} intent={r.intent}/{r.sub_intent or '-'}")
        lines.append(f"      Q: {q_preview}")
    lines.append(RESET)
    return "\n".join(lines)


def main() -> int:
    # Silence the logger for this interactive session — we want clean output
    setup_logging(level="WARNING")

    print_banner()

    print("Loading generator (this takes ~1 second)...")
    generator = Generator()
    print(f"{GREEN}✓ Ready.{RESET}\n")

    # Session state
    last_reply: Optional[str] = None
    query_count = 0
    total_time = 0.0
    show_retrieval = False

    while True:
        try:
            query = input(f"{BOLD}{CYAN}You: {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C or Ctrl+D — exit gracefully
            print(f"\n{GRAY}[session ended]{RESET}")
            break

        if not query:
            continue  # empty input: just re-prompt, don't invoke the bot

        # Handle commands
        if query in ("/quit", "/q", "/exit"):
            print(f"{GRAY}[session ended]{RESET}")
            break

        if query == "/help":
            print_help()
            continue

        if query == "/last":
            if last_reply:
                print(f"{GREEN}Bot (last reply):{RESET} {last_reply}\n")
            else:
                print(f"{YELLOW}No previous reply yet.{RESET}\n")
            continue

        if query == "/stats":
            avg = total_time / query_count if query_count else 0.0
            print(f"{GRAY}   queries: {query_count}   avg time: {avg:.2f}s{RESET}\n")
            continue

        if query == "/retrieval":
            show_retrieval = not show_retrieval
            state = "ON" if show_retrieval else "OFF"
            print(f"{GRAY}   retrieval debug: {state}{RESET}\n")
            continue

        # Optional: show retrieval debug BEFORE calling the generator
        if show_retrieval:
            print(format_retrieval_debug(generator.retriever, query))

        # Call the generator, time it
        start = time.perf_counter()
        try:
            reply = generator.generate(query)
        except Exception as exc:
            print(f"{YELLOW}[error: {type(exc).__name__}: {exc}]{RESET}\n")
            continue
        elapsed = time.perf_counter() - start

        # Track stats
        query_count += 1
        total_time += elapsed
        last_reply = reply

        # Print the reply with the elapsed time as a subtle annotation
        print(f"{GREEN}Bot:{RESET} {reply}")
        print(f"{GRAY}   [{elapsed:.2f}s]{RESET}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())