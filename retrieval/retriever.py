"""
Retrieve relevant KB entries given a user query.
Loads the FAISS index once, then serves queries instantly.
"""
import json
from typing import List, Dict, Any
from dataclasses import dataclass

import faiss
import numpy as np

from config import (
    FAISS_INDEX_PATH,
    METADATA_PATH,
    TOP_K,
    SIMILARITY_THRESHOLD,
)
from ingestion.embedder import embed_text


@dataclass
class RetrievalResult:
    """A single retrieved entry with its similarity score."""
    id: str
    intent: str
    sub_intent: str
    language: str
    question: str
    answer: str
    attachments: List[Dict[str, str]]
    score: float  # cosine similarity: 0 (unrelated) to 1 (identical)

    def __repr__(self) -> str:
        return f"<Result id={self.id} score={self.score:.3f}>"


class Retriever:
    """
    Loads the FAISS index + metadata, serves queries.
    Instantiate ONCE, then call .search() many times.
    """

    def __init__(self):
        if not FAISS_INDEX_PATH.exists():
            raise FileNotFoundError(
                f"FAISS index not found at {FAISS_INDEX_PATH}. "
                f"Run: python -m ingestion.indexer"
            )
        if not METADATA_PATH.exists():
            raise FileNotFoundError(
                f"Metadata not found at {METADATA_PATH}. "
                f"Run: python -m ingestion.indexer"
            )

        # Load FAISS index
        self.index = faiss.read_index(str(FAISS_INDEX_PATH))

        # Load metadata
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            self.metadata = json.load(f)

        self.entries = self.metadata["entries"]
        print(f"✅ Retriever loaded: {self.index.ntotal} vectors indexed")

    def search(
        self,
        query: str,
        top_k: int = TOP_K,
        threshold: float = SIMILARITY_THRESHOLD,
    ) -> List[RetrievalResult]:
        """
        Search the index for the top-K most similar entries to the query.
        Returns results above the similarity threshold.
        """
        if not query or not query.strip():
            return []

        # Embed the query
        query_vector = embed_text(query)

        # Convert to numpy + normalize (same treatment as indexed vectors)
        query_array = np.array([query_vector], dtype=np.float32)
        faiss.normalize_L2(query_array)

        # Search
        scores, indices = self.index.search(query_array, top_k)

        # Build results, filtering by threshold
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:  # FAISS returns -1 for empty slots
                continue
            if score < threshold:
                continue

            entry = self.entries[idx]
            results.append(
                RetrievalResult(
                    id=entry["id"],
                    intent=entry["intent"],
                    sub_intent=entry.get("sub_intent", ""),
                    language=entry["language"],
                    question=entry["question"],
                    answer=entry["answer"],
                    attachments=entry.get("attachments", []),
                    score=float(score),
                )
            )

        return results


if __name__ == "__main__":
    # Interactive test — try a few queries
    print("=" * 60)
    print("🔍 RETRIEVER TEST")
    print("=" * 60)

    retriever = Retriever()

    test_queries = [
        "interior design er cost koto?",
        "আপনাদের অফিস কোথায়?",
        "what is the payment schedule?",
        "কিচেন ক্যাবিনেটের দাম",
        "Can you work in Chittagong?",
        "site visit ki free?",
    ]

    for query in test_queries:
        print(f"\n🔎 Query: {query}")
        results = retriever.search(query, top_k=3)

        if not results:
            print("   ❌ No results above threshold")
            continue

        for i, r in enumerate(results, start=1):
            print(f"   {i}. [{r.score:.3f}] ({r.language}) {r.question}")
            print(f"      → {r.answer[:80]}...")