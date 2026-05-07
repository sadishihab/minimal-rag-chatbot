"""
Audit newline usage in knowledge_base.json answer fields.
Read-only — does not modify the KB.

Run from project root:
    python tests/audit_newlines.py
"""
import json
import re
from pathlib import Path
from collections import Counter

# Adjust this if your KB lives elsewhere
KB_PATH = Path("data/knowledge_base.json")


def categorize(answer: str) -> str:
    """Classify how an answer uses newlines."""
    if "\n" not in answer:
        return "no_newline"
    if "\n\n\n" in answer:
        return "triple_plus_newline"  # likely accidental excess whitespace
    if "\n\n" in answer:
        return "double_newline_only"  # paragraph breaks
    # Only single \n at this point
    # Check if line after \n starts with bullet/dash/digit (intentional list)
    lines_after_nl = re.findall(r"\n(.)", answer)
    if any(ch in "-•*১২৩৪৫৬৭৮৯০0123456789" for ch in lines_after_nl):
        return "single_newline_list"
    return "single_newline_prose"  # most likely accidental


def preview(text: str, max_len: int = 120) -> str:
    """Show \\n as visible literal so we can see them."""
    visible = text.replace("\n", "⏎\n")  # arrow shows where \n is
    if len(visible) > max_len:
        visible = visible[:max_len] + "..."
    return visible


def main():
    if not KB_PATH.exists():
        print(f"🔴 KB not found at: {KB_PATH.resolve()}")
        print("   Run this script from your project root (C:\\Users\\sadis\\minimal_rag)")
        return

    with open(KB_PATH, "r", encoding="utf-8") as f:
        kb = json.load(f)

    entries = kb["entries"]
    total = len(entries)

    # Categorize every entry
    buckets = {
        "no_newline": [],
        "double_newline_only": [],
        "single_newline_list": [],
        "single_newline_prose": [],
        "triple_plus_newline": [],
    }
    for e in entries:
        cat = categorize(e["answer"])
        buckets[cat].append(e)

    # Summary
    print("=" * 70)
    print(f"NEWLINE AUDIT — {total} total entries")
    print("=" * 70)
    for cat, items in buckets.items():
        print(f"  {cat:30s}  {len(items):4d}  ({100*len(items)/total:.1f}%)")
    print()

    # Show samples from each "newline-having" category
    for cat in ["triple_plus_newline", "single_newline_prose", "single_newline_list", "double_newline_only"]:
        items = buckets[cat]
        if not items:
            continue
        print("=" * 70)
        print(f"CATEGORY: {cat}  ({len(items)} entries)")
        print("=" * 70)
        # Show up to 5 samples per category
        for e in items[:5]:
            print(f"\n  ID: {e['id']}  | intent: {e['intent']}  | lang: {e['language']}")
            print(f"  Q:  {e['question'][:80]}")
            print(f"  A:  {preview(e['answer'])}")
        if len(items) > 5:
            print(f"\n  ... and {len(items) - 5} more in this category")
        print()

    # List ALL IDs of "single_newline_prose" — the most suspicious category
    if buckets["single_newline_prose"]:
        print("=" * 70)
        print("ALL IDs with single_newline_prose (likely accidental):")
        print("=" * 70)
        for e in buckets["single_newline_prose"]:
            print(f"  - {e['id']}")
        print()


if __name__ == "__main__":
    main()