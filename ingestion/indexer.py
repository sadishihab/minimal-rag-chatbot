"""
Build the FAISS vector index from the knowledge base.
This is a ONE-TIME operation — run it once, then the search layer uses the saved index.
"""
import json
import time
from typing import List, Dict, Any

import faiss
import numpy as np

from config import (
    EMBEDDING_DIMENSIONS,
    FAISS_INDEX_PATH,
    METADATA_PATH,
    VECTOR_STORE_DIR,
)
from ingestion.loader import load_entries, KnowledgeBaseEntry
from ingestion.embedder import embed_batch


def build_index() -> None:
    """
    Full pipeline: load → embed → index → save.
    """
    print("=" * 60)
    print("🏗️  BUILDING FAISS INDEX")
    print("=" * 60)

    # Step 1: Load entries
    print("\n📖 Step 1/4: Loading knowledge base...")
    entries: List[KnowledgeBaseEntry] = load_entries()
    print(f"   Loaded {len(entries)} entries")

    # Step 2: Embed all questions
    print(f"\n🔤 Step 2/4: Embedding {len(entries)} questions via OpenAI...")
    print("   (This will take ~30 seconds and cost ~$0.001)")

    texts_to_embed = [e.searchable_text() for e in entries]

    start = time.time()
    vectors = embed_batch(texts_to_embed)
    elapsed = time.time() - start

    print(f"   ✅ Embedded in {elapsed:.1f}s")
    print(f"   Each vector has {len(vectors[0])} dimensions")

    # Step 3: Build FAISS index
    print("\n🔧 Step 3/4: Building FAISS index...")

    # Convert to numpy array (FAISS requires float32)
    vector_array = np.array(vectors, dtype=np.float32)

    # Normalize vectors — enables cosine similarity via inner product
    # This is a standard trick: cosine similarity of normalized vectors = inner product
    faiss.normalize_L2(vector_array)

    # IndexFlatIP = Inner Product index (exact search, no approximation)
    # For 224 vectors this is perfectly fast — no need for approximate search
    index = faiss.IndexFlatIP(EMBEDDING_DIMENSIONS)
    index.add(vector_array)

    print(f"   Index built with {index.ntotal} vectors")

    # Step 4: Save to disk
    print("\n💾 Step 4/4: Saving index and metadata to disk...")

    # Make sure directory exists
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

    # Save FAISS index
    faiss.write_index(index, str(FAISS_INDEX_PATH))
    print(f"   Index saved: {FAISS_INDEX_PATH}")

    # Save metadata (maps vector index → entry details)
    metadata = {
        "version": "1.0",
        "total_vectors": len(entries),
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "entries": [
            {
                "vector_index": i,
                "id": e.id,
                "intent": e.intent,
                "sub_intent": e.sub_intent,
                "language": e.language,
                "question": e.question,
                "answer": e.answer,
                "attachments": e.attachments,
            }
            for i, e in enumerate(entries)
        ],
    }

    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"   Metadata saved: {METADATA_PATH}")

    # Final summary
    print("\n" + "=" * 60)
    print("✅ INDEX BUILD COMPLETE")
    print("=" * 60)
    print(f"   Total vectors:     {index.ntotal}")
    print(f"   Dimensions:        {EMBEDDING_DIMENSIONS}")
    print(f"   Index file size:   {FAISS_INDEX_PATH.stat().st_size / 1024:.1f} KB")
    print(f"   Metadata size:     {METADATA_PATH.stat().st_size / 1024:.1f} KB")
    print(f"\n💡 Your vector store is ready. Search is now instant & free.")


if __name__ == "__main__":
    build_index()