# data/

This folder is where the **knowledge base** lives.

## Files

- `knowledge_base.sample.json` — A small fictional sample (8 entries) showing the expected schema. Safe to read and modify.
- `knowledge_base.json` — **Your real KB.** Not included in this repo. Create it yourself by copying the sample and replacing entries with your own content.

## Quickstart

```bash
# Copy the sample as your starting KB
cp data/knowledge_base.sample.json data/knowledge_base.json

# Edit it with your real data, then build the FAISS index:
python -m ingestion.indexer
```

## Schema

Each entry must include:

| Field        | Type   | Notes                                                |
|--------------|--------|------------------------------------------------------|
| `id`         | string | Unique across the whole KB                           |
| `intent`     | string | Top-level category (pricing, contact, process, etc.) |
| `sub_intent` | string | Optional sub-category                                |
| `language`   | string | One of: `bangla`, `banglish`, `english`              |
| `question`   | string | The question as a customer would type it             |
| `answer`     | string | The reply (always in formal Bangla in this project)  |
| `attachments`| array  | Optional URLs/labels                                 |

## Why three languages per Q&A?

Bangladeshi customers on Messenger actually mix all three — Bangla, Banglish (Bangla written in Latin script), and English — sometimes within the same conversation. Embedding-based semantic search retrieves best when there's at least one entry stored in a script close to what the customer typed. So each Q&A is duplicated across the three languages, all pointing to the same Bangla answer.