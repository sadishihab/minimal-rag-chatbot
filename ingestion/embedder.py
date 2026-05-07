"""
Convert text to vectors using OpenAI's embedding API.
"""
from typing import List
from openai import OpenAI

from config import OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS


# Initialize the OpenAI client once when this module loads
_client = OpenAI(api_key=OPENAI_API_KEY)


def embed_text(text: str) -> List[float]:
    """
    Convert a single text string to a 1536-dimensional vector.
    Used at runtime for customer queries.
    """
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")

    response = _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )

    return response.data[0].embedding


def embed_batch(texts: List[str], batch_size: int = 100) -> List[List[float]]:
    """
    Convert multiple text strings to vectors in batches.
    Used at indexing time to embed the whole knowledge base.

    OpenAI allows up to 2048 inputs per request, but 100 is a safer batch size.
    """
    if not texts:
        return []

    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        print(f"  Embedding batch {i//batch_size + 1} ({len(batch)} items)...")

        response = _client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
        )

        # Sort by index to ensure order matches input
        batch_embeddings = sorted(response.data, key=lambda x: x.index)
        all_embeddings.extend([item.embedding for item in batch_embeddings])

    return all_embeddings


if __name__ == "__main__":
    # Quick test — make ONE API call to verify everything works
    print("🧪 Testing OpenAI embedding API with a single call...\n")

    test_text = "interior design er cost koto?"
    print(f"Input:  {test_text}")

    vector = embed_text(test_text)

    print(f"Output: vector with {len(vector)} dimensions")
    print(f"First 5 values: {vector[:5]}")
    print(f"Expected dimensions: {EMBEDDING_DIMENSIONS}")

    assert len(vector) == EMBEDDING_DIMENSIONS, "Vector size mismatch!"
    print("\n✅ Embedding API works correctly!")