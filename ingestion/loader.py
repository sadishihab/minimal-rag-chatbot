"""
Load and validate the knowledge base JSON.
Returns clean entries ready to be embedded.
"""
import json
from pathlib import Path
from typing import List, Dict, Any

from config import KNOWLEDGE_BASE_PATH


class KnowledgeBaseEntry:
    """Represents a single Q&A entry from the KB."""

    def __init__(self, raw: Dict[str, Any]):
        self.id: str = raw["id"]
        self.intent: str = raw["intent"]
        self.sub_intent: str = raw.get("sub_intent", "")
        self.language: str = raw["language"]
        self.question: str = raw["question"]
        self.answer: str = raw["answer"]
        self.attachments: List[Dict[str, str]] = raw.get("attachments", [])

    def searchable_text(self) -> str:
        """
        Returns the text used for semantic search.
        We embed the QUESTION (not the answer) because users send questions.
        """
        return self.question

    def __repr__(self) -> str:
        return f"<KBEntry id={self.id} intent={self.intent} lang={self.language}>"


def load_knowledge_base(path: Path = KNOWLEDGE_BASE_PATH) -> Dict[str, Any]:
    """
    Load the raw JSON file from disk.
    Returns the full parsed dictionary.
    """
    if not path.exists():
        raise FileNotFoundError(f"Knowledge base not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        kb = json.load(f)

    return kb


def load_entries(path: Path = KNOWLEDGE_BASE_PATH) -> List[KnowledgeBaseEntry]:
    """
    Load, validate, and return entries as KnowledgeBaseEntry objects.
    Raises ValueError if validation fails.
    """
    kb = load_knowledge_base(path)

    if "entries" not in kb:
        raise ValueError("JSON has no 'entries' key.")

    raw_entries = kb["entries"]
    entries = []
    errors = []
    seen_ids = set()

    required_fields = ["id", "intent", "language", "question", "answer"]

    for idx, raw in enumerate(raw_entries):
        # Check required fields
        missing = [f for f in required_fields if f not in raw or not raw[f]]
        if missing:
            errors.append(f"Entry #{idx} missing fields: {missing}")
            continue

        # Check for duplicate IDs
        if raw["id"] in seen_ids:
            errors.append(f"Duplicate ID: {raw['id']}")
            continue

        seen_ids.add(raw["id"])
        entries.append(KnowledgeBaseEntry(raw))

    if errors:
        raise ValueError(
            f"Found {len(errors)} validation errors:\n  " + "\n  ".join(errors[:10])
        )

    return entries


def get_stats(entries: List[KnowledgeBaseEntry]) -> Dict[str, Any]:
    """Return useful stats about the loaded KB."""
    from collections import Counter

    return {
        "total_entries": len(entries),
        "by_intent": dict(Counter(e.intent for e in entries)),
        "by_language": dict(Counter(e.language for e in entries)),
        "avg_answer_length": sum(len(e.answer.split()) for e in entries) / len(entries),
        "with_attachments": sum(1 for e in entries if e.attachments),
    }


if __name__ == "__main__":
    # Quick test when running this file directly
    print("📖 Loading knowledge base...")
    entries = load_entries()
    print(f"✅ Loaded {len(entries)} entries successfully\n")

    stats = get_stats(entries)
    print("📊 Stats:")
    print(f"   Total entries:       {stats['total_entries']}")
    print(f"   Entries w/ links:    {stats['with_attachments']}")
    print(f"   Avg answer length:   {stats['avg_answer_length']:.1f} words\n")

    print("   By intent:")
    for intent, count in sorted(stats['by_intent'].items()):
        print(f"     - {intent}: {count}")

    print("\n   By language:")
    for lang, count in sorted(stats['by_language'].items()):
        print(f"     - {lang}: {count}")

    print("\n🔍 Sample entry:")
    sample = entries[0]
    print(f"   ID: {sample.id}")
    print(f"   Q:  {sample.question}")
    print(f"   A:  {sample.answer[:80]}...")