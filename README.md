# Minimal RAG — Multilingual Customer Service Chatbot for Facebook Messenger

A production-ready Retrieval-Augmented Generation (RAG) chatbot built for **Minimal Limited**, an interior design firm in Dhaka, Bangladesh. Customers message the company's Facebook Page in **Bangla, Banglish, or English** — sometimes mixing all three — and the bot retrieves the relevant answer from a curated knowledge base and replies in **formal Bangla** with the appropriate tone, numerals, and brand-name conventions.

> **Status:** Active development. Retrieval + generation pipeline complete; Messenger integration deployed via a 4-stage safe-rollout workflow (terminal → local web → test FB page → live page).

---

## The Problem

Bangladeshi customers don't pick one language and stick to it. A single Messenger thread can swing between Bangla script (`আপনাদের দাম কেমন?`), Banglish (`apnader dam kemon?`), and English (`what are your prices?`). Most off-the-shelf chatbots either reply in the customer's input language (inconsistent brand voice) or require strict language separation (poor UX).

This bot solves that by:

- Storing each Q&A in **all three languages** in the knowledge base, so semantic search hits regardless of how the customer types
- **Always replying in formal Bangla** with `আপনি`, Bangla numerals, and brand-correct exceptions (phone numbers, postal codes, and trademarks stay in Latin script)
- Falling back gracefully to a "share your number, our manager will call" message when the KB doesn't cover a query — because **the bot is an assistant to humans, not a replacement**

---

## Architecture
┌─────────────────────────────────────────────────────────────────────┐
│                        Customer (Messenger)                          │
│      Bangla / Banglish / English — any mix, casual or formal        │
└────────────────────────────────┬────────────────────────────────────┘
│ webhook POST
▼
┌─────────────────────────────────────────────────────────────────────┐
│              FastAPI Webhook  (api/server.py)                       │
│   • verify FB signature   • route to message_classifier             │
│   • check pause_state (human-takeover guard)                        │
└────────────────────────────────┬────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────────┐
│   Embed query  →  text-embedding-3-small (1536 dims)                │
│                          (ingestion/embedder.py)                    │
└────────────────────────────────┬────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────────┐
│   FAISS IndexFlatIP  →  top-k cosine-similarity matches             │
│                          (retrieval/retriever.py)                   │
│   filter: similarity ≥ threshold,  k = 3                            │
└────────────────────────────────┬────────────────────────────────────┘
│ retrieved KB entries
▼
┌─────────────────────────────────────────────────────────────────────┐
│   Build prompt with strict Bangla-output system rules               │
│                       (generation/prompt_builder.py)                │
└────────────────────────────────┬────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────────┐
│   GPT-4o-mini  →  formal-Bangla reply                               │
│                       (generation/generator.py)                     │
│   post-processing: sanitizer, formatter, phone_detector             │
└────────────────────────────────┬────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────────┐
│   Send reply via Messenger Send API  (api/send_api.py)              │
└─────────────────────────────────────────────────────────────────────┘

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ (developed on 3.13) | First-class async, modern typing |
| LLM | OpenAI `gpt-4o-mini` | Cheap, fast, handles Bangla well |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) | Excellent multilingual quality at low cost |
| Vector store | FAISS (`IndexFlatIP`, L2-normalized) | Local, fast, exact search for KB-scale data |
| API framework | FastAPI + Uvicorn | Async webhook handling, automatic OpenAPI docs |
| Messenger | Facebook Graph API (Send API + webhooks) | Native Messenger integration |
| Config | `python-dotenv` | Standard env-var management |
| Testing | `pytest` | Schema/data validation + integration tests |
| Container | Docker | Reproducible deployment |

**Notably absent:** LangChain, LlamaIndex, ChromaDB, Pinecone. Every part of the pipeline is hand-written so the data flow stays inspectable and the dependency tree stays small.

---

## Key Design Decisions

**Why no LangChain?** For a focused single-domain RAG with a stable schema, framework abstractions hide more than they help. The full retrieval-and-generation logic is under 300 lines and every step is debuggable in isolation.

**Why FAISS over Chroma/Pinecone?** With ~200–500 KB entries, exact search via `IndexFlatIP` is instant and free. No external service, no network latency, no hosted-DB billing. The index rebuilds in ~30 seconds when the KB changes.

**Why embed the question, not the answer?** Customers send questions, not answers. Question-to-question similarity matches better than question-to-answer. The answer is metadata — retrieved by index, not searched.

**Why three language entries per Q&A?** Embedding-based search retrieves best when the query and the indexed text share a script and idiom. Storing one Q&A as Bangla + Banglish + English versions makes retrieval robust to whatever the customer types, without depending on translation as a brittle middle layer.

**Why a similarity threshold?** Below ~0.3 cosine similarity, retrieved entries are noise. Better to fall back to "let our manager call you" than to confidently answer the wrong question.

---

## Project Structure
minimal_rag/
├── data/                    # Knowledge base
│   ├── knowledge_base.sample.json   # Fictional sample (8 entries)
│   └── README.md
├── ingestion/               # Load, validate, embed, index
│   ├── loader.py
│   ├── embedder.py
│   └── indexer.py
├── retrieval/               # Vector search
│   └── retriever.py
├── generation/              # Prompt building + LLM call + post-processing
│   ├── prompt_builder.py
│   ├── generator.py
│   ├── formatter.py
│   ├── sanitizer.py
│   └── phone_detector.py
├── api/                     # FastAPI webhook + Messenger client
│   ├── server.py
│   ├── messenger.py
│   ├── send_api.py
│   ├── message_classifier.py
│   ├── pause_state.py
│   └── request_context.py
├── tests/                   # pytest suites + interactive CLI
│   ├── test_loader.py
│   ├── test_api.py
│   ├── test_message_classifier.py
│   ├── chat_cli.py          # interactive CLI for exploratory testing
│   ├── test_catalog.py      # eval runner (reads tests/catalog.yaml)
│   └── audit_newlines.py    # KB hygiene utility
├── vector_store/            # FAISS index (gitignored, regenerated locally)
├── config.py                # Central config: paths, models, thresholds
├── logger.py                # Structured logging
├── main.py                  # Entry point
├── Dockerfile
├── entrypoint.sh
├── .env.example             # Copy to .env and fill in your keys
└── requirements.txt

---

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/<your-username>/minimal-rag.git
cd minimal-rag
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your OpenAI API key (and FB tokens if deploying to Messenger)
```

### 3. Add your knowledge base

```bash
cp data/knowledge_base.sample.json data/knowledge_base.json
# Edit data/knowledge_base.json with your own Q&A entries
```

### 4. Build the FAISS index

```bash
python -m ingestion.indexer
```

### 5. Try it locally

```bash
# Interactive CLI (no FastAPI / no Messenger needed)
python -m tests.chat_cli
```

### 6. Run the FastAPI server

```bash
uvicorn api.server:app --reload --port 8000
```

For Messenger deployment, see Facebook's [Messenger Platform docs](https://developers.facebook.com/docs/messenger-platform/) for webhook configuration.

---

## Roadmap

- [x] Knowledge base schema, loader, validation tests
- [x] OpenAI embedding pipeline + FAISS index
- [x] Retrieval with similarity threshold and graceful fallback
- [x] Prompt engineering for strict formal-Bangla output
- [x] FastAPI webhook + Facebook Messenger integration
- [x] Human-takeover pause state + message classifier
- [x] Interactive CLI for exploratory testing
- [x] Catalog-based eval system (137 queries × 28 categories)
- [ ] Conversation memory (multi-turn context)
- [ ] Admin dashboard for live conversation monitoring
- [ ] Auto-summarization of long threads for human handover

---

## License

This repository contains code only. The original knowledge base and any deployed assets remain proprietary to Minimal Limited.

Code is released under the MIT License — see `LICENSE`.

---

## Author

Built by **Sadis Khan** ([portfolio link]) — a portfolio project demonstrating production RAG architecture, multilingual NLP for Bangla/Banglish/English, and Messenger Platform integration.

The fictional sample data in this repo is for demonstration only.