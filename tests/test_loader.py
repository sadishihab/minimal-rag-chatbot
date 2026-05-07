"""
Tests for ingestion/loader.py
Run with: pytest tests/test_loader.py -v
"""
import pytest
from ingestion.loader import (
    KnowledgeBaseEntry,
    load_knowledge_base,
    load_entries,
    get_stats,
)


# ============================================================
# Basic loading tests
# ============================================================

def test_load_knowledge_base_returns_dict():
    """The raw loader should return a dictionary with 'entries' key."""
    kb = load_knowledge_base()
    assert isinstance(kb, dict)
    assert "entries" in kb
    assert "company" in kb


def test_load_entries_returns_non_empty_list():
    """The entry loader should return a list of entries."""
    entries = load_entries()
    assert isinstance(entries, list)
    assert len(entries) > 0


def test_load_entries_returns_entry_objects():
    """Each item should be a KnowledgeBaseEntry instance."""
    entries = load_entries()
    for entry in entries:
        assert isinstance(entry, KnowledgeBaseEntry)


# ============================================================
# Data integrity tests
# ============================================================

def test_all_entries_have_required_fields():
    """Every entry must have id, intent, language, question, answer."""
    entries = load_entries()
    for entry in entries:
        assert entry.id, f"Entry has empty ID"
        assert entry.intent, f"Entry {entry.id} has empty intent"
        assert entry.language, f"Entry {entry.id} has empty language"
        assert entry.question, f"Entry {entry.id} has empty question"
        assert entry.answer, f"Entry {entry.id} has empty answer"


def test_all_entry_ids_are_unique():
    """No two entries should share the same ID."""
    entries = load_entries()
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids)), "Duplicate IDs found"


def test_languages_are_valid():
    """Language field should only be bangla, banglish, or english."""
    entries = load_entries()
    valid_languages = {"bangla", "banglish", "english"}
    for entry in entries:
        assert entry.language in valid_languages, (
            f"Entry {entry.id} has invalid language: {entry.language}"
        )


def test_searchable_text_returns_question():
    """searchable_text() should return the question, not the answer."""
    entries = load_entries()
    for entry in entries:
        assert entry.searchable_text() == entry.question


# ============================================================
# Stats tests
# ============================================================

def test_get_stats_returns_expected_keys():
    """get_stats should return all expected keys."""
    entries = load_entries()
    stats = get_stats(entries)

    expected_keys = {
        "total_entries",
        "by_intent",
        "by_language",
        "avg_answer_length",
        "with_attachments",
    }
    assert set(stats.keys()) == expected_keys


def test_stats_counts_match_entries():
    """Sum of language counts should equal total entries."""
    entries = load_entries()
    stats = get_stats(entries)

    assert stats["total_entries"] == len(entries)
    assert sum(stats["by_language"].values()) == len(entries)
    assert sum(stats["by_intent"].values()) == len(entries)


# ============================================================
# Business logic tests (catches data errors early)
# ============================================================

def test_kb_has_pricing_intent():
    """KB must cover pricing — this is the most common customer query."""
    entries = load_entries()
    intents = {e.intent for e in entries}
    assert "pricing" in intents


def test_kb_has_contact_intent():
    """KB must have contact info."""
    entries = load_entries()
    intents = {e.intent for e in entries}
    assert "contact" in intents


def test_all_answers_are_concise_enough():
    """No answer should be longer than 100 words (Messenger is casual)."""
    entries = load_entries()
    for entry in entries:
        word_count = len(entry.answer.split())
        assert word_count <= 100, (
            f"Entry {entry.id} has {word_count} words (>100)"
        )