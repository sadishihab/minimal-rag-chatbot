# Minimal RAG — Multilingual Customer Service Chatbot for Facebook Messenger

A production-ready Retrieval-Augmented Generation (RAG) chatbot built for **Minimal Limited**, an interior design firm in Dhaka, Bangladesh. Customers message the company's Facebook Page in **Bangla, Banglish, or English** — sometimes mixing all three — and the bot retrieves the relevant answer from a curated knowledge base and replies in **formal Bangla** with the appropriate tone, numerals, and brand-name conventions.

> **Status:** Live on a private test Facebook Page undergoing structured user testing. Knowledge base is being refined based on tester feedback before rollout to the production Messenger page. Deployed on DigitalOcean via Docker, behind nginx with TLS. Human-takeover (rep-pause) system active.

---

## The Problem

Bangladeshi customers don't pick one language and stick to it. A single Messenger thread can swing between Bangla script (`আপনাদের দাম কেমন?`), Banglish (`apnader dam kemon?`), and English (`what are your prices?`). Most off-the-shelf chatbots either reply in the customer's input language (inconsistent brand voice) or require strict language separation (poor UX).

This bot solves that by:

- Storing each Q&A in **all three languages** in the knowledge base, so semantic search hits regardless of how the customer types
- **Always replying in formal Bangla** with `আপনি`, Bangla numerals, and brand-correct exceptions (phone numbers, postal codes, and trademarks stay in Latin script)
- Falling back gracefully to a "share your number, our manager will call" message when the KB doesn't cover a query — because **the bot is an assistant to humans, not a replacement**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Customer (Messenger)                          │
│      Bangla / Banglish / English — any mix, casual or formal        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ webhook POST
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│              FastAPI Webhook  (api/server.py)                        │
│   • verify FB HMAC-SHA256 signature                                  │
│   • check pause_state (human-takeover guard)                         │
│   • route to message_classifier                                      │
│     → emoji/sticker → ধন্যবাদ (no RAG, no pause)                   │
│     → attachment/URL → handoff message + pause thread                │
│     → phone number → canned ack + bypass RAG                        │
│     → echo (rep reply) → pause thread for 7 days                    │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ plain text query
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│   Embed query  →  text-embedding-3-small (1536 dims)                 │
│                          (ingestion/embedder.py)                     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│   FAISS IndexFlatIP  →  top-k cosine-similarity matches              │
│                          (retrieval/retriever.py)                    │
│   filter: similarity ≥ 0.3,  k = 4                                  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ retrieved KB entries
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│   Build prompt with strict Bangla-output system rules                │
│                       (generation/prompt_builder.py)                 │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│   GPT-4o-mini  →  formal-Bangla reply                                │
│                       (generation/generator.py)                      │
│   post-processing: sanitizer, formatter, phone_detector              │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│   Send reply via Messenger Send API  (api/send_api.py)               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.13 | First-class async, modern typing |
| LLM | OpenAI `gpt-4o-mini` | Cheap, fast, handles Bangla well |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) | Excellent multilingual quality at low cost |
| Vector store | FAISS (`IndexFlatIP`, L2-normalized) | Local, fast, exact search for KB-scale data |
| API framework | FastAPI + Uvicorn | Async webhook handling, automatic OpenAPI docs |
| Messenger | Facebook Graph API (Send API + webhooks) | Native Messenger integration |
| Config | `python-dotenv` | Standard env-var management |
| Testing | `pytest` + custom catalog runner | Schema validation + 137-query eval suite |
| Container | Docker (multi-stage, non-root) | Reproducible deployment |
| Registry | GitHub Container Registry (GHCR) | Free, integrated with GitHub |
| Hosting | DigitalOcean (Ubuntu 24.04, Singapore) | Low-latency for BD customers |
| Reverse proxy | nginx + Let's Encrypt TLS | HTTPS termination |

**Notably absent:** LangChain, LlamaIndex, ChromaDB, Pinecone. Every part of the pipeline is hand-written so the data flow stays inspectable and the dependency tree stays small.

---

## Key Design Decisions

**Why no LangChain?** For a focused single-domain RAG with a stable schema, framework abstractions hide more than they help. The full retrieval-and-generation logic is under 300 lines and every step is debuggable in isolation.

**Why FAISS over Chroma/Pinecone?** With ~300–500 KB entries, exact search via `IndexFlatIP` is instant and free. No external service, no network latency, no hosted-DB billing. The index rebuilds in ~30 seconds when the KB changes.

**Why embed the question, not the answer?** Customers send questions, not answers. Question-to-question similarity matches better than question-to-answer. The answer is metadata — retrieved by index, not searched.

**Why three language entries per Q&A?** Embedding-based search retrieves best when the query and the indexed text share a script and idiom. Storing one Q&A as Bangla + Banglish + English versions makes retrieval robust to whatever the customer types, without depending on translation as a brittle middle layer.

**Why a similarity threshold?** Below ~0.3 cosine similarity, retrieved entries are noise. Better to fall back to "let our manager call you" than to confidently answer the wrong question.

**Why a human-takeover pause system?** The bot is a first-responder, not a replacement. When a human rep replies via Page Inbox, the bot detects the echo event, identifies it as a rep reply (by `app_id` mismatch), and pauses itself for 7 days on a sliding window. Attachments, URLs, and phone numbers also trigger handoff. The bot stays completely silent while paused — the rep owns the thread.

---

## Project Structure

```
minimal_rag/
├── data/                         # Knowledge base (gitignored — proprietary)
│   ├── knowledge_base.sample.json   # Fictional sample (8 entries)
│   └── README.md
├── ingestion/                    # Load, validate, embed, index
│   ├── loader.py
│   ├── embedder.py
│   └── indexer.py
├── retrieval/                    # Vector search
│   └── retriever.py
├── generation/                   # Prompt building + LLM call + post-processing
│   ├── prompt_builder.py
│   ├── generator.py
│   ├── formatter.py
│   ├── sanitizer.py
│   └── phone_detector.py
├── api/                          # FastAPI webhook + Messenger client
│   ├── server.py
│   ├── messenger.py
│   ├── send_api.py
│   ├── message_classifier.py
│   ├── pause_state.py
│   └── request_context.py
├── tests/                        # pytest suites + eval tools
│   ├── test_loader.py
│   ├── test_api.py
│   ├── test_message_classifier.py
│   ├── chat_cli.py               # interactive CLI for exploratory testing
│   ├── test_catalog.py           # eval runner (reads tests/catalog.yaml)
│   ├── catalog.yaml              # 137 queries across 28 categories
│   └── audit_newlines.py         # KB hygiene utility
├── vector_store/                 # FAISS index (gitignored, regenerated locally)
├── config.py                     # Central config: paths, models, thresholds
├── logger.py                     # Structured logging
├── Dockerfile                    # Multi-stage build, non-root appuser
├── entrypoint.sh                 # Conditional indexer + uvicorn startup
├── .env.example                  # Copy to .env and fill in your keys
└── requirements.txt
```

---

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/sadishihab/minimal-rag-chatbot.git
cd minimal-rag-chatbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

Required keys:

```
OPENAI_API_KEY=sk-...
FACEBOOK_PAGE_ACCESS_TOKEN=...
FACEBOOK_APP_SECRET=...
FACEBOOK_VERIFY_TOKEN=...
FACEBOOK_APP_ID=...
```

### 3. Add your knowledge base

```bash
cp data/knowledge_base.sample.json data/knowledge_base.json
# Edit with your own Q&A entries
```

### 4. Build the FAISS index

```bash
python -m ingestion.indexer
```

### 5. Try it locally

```bash
# Interactive CLI — no FastAPI or Messenger needed
python -m tests.chat_cli

# Or start the full FastAPI server
uvicorn api.server:app --reload --port 8000
```

### 6. Run tests

```bash
pytest tests/test_loader.py tests/test_message_classifier.py -v
```

---

## Deploy Workflow

```
Edit code locally (neovim + tmux)
    → test locally (uvicorn)
    → git push → GitHub
    → docker build -t ghcr.io/sadishihab/minimal-rag:latest .
    → docker push ghcr.io/sadishihab/minimal-rag:latest
    → SSH into DO droplet
    → docker pull ghcr.io/sadishihab/minimal-rag:latest
    → docker stop minimal-rag && docker run ...
```

The DigitalOcean droplet runs the container behind nginx with TLS, exposed at `chat.minimallimited.com`. Secrets are injected as environment variables at `docker run` time — never baked into the image.

---

## Knowledge Base

The production KB has **336 entries across 18+ intents** in three languages (Bangla, Banglish, English). Each intent typically has 3 entries — one per language — so FAISS hits regardless of how the customer types.

Sample intents: `pricing`, `main_packages`, `size_based_pricing`, `materials_brands`, `package_materials`, `interior_essence_package`, `location_coverage`, `process`, `specific_rooms_scope`, `site_visit`, `services_offered`, `contact`, `custom_furniture`, `payment_schedule`, `home_interior`, `office_interior`, `general_inquiry`.

The KB itself is proprietary and gitignored. The `data/knowledge_base.sample.json` contains fictional sample data for demonstration.

---

## Evaluation

A catalog-based eval system runs 137 queries across 28 categories through the full RAG pipeline and produces a markdown report with retrieval scores, matched intents, and bot replies for human review.

```bash
python -m tests.test_catalog
# Output: tests/catalog_report_YYYY-MM-DD_HH-MM.md
```

Categories cover: greetings, pricing (general + room-specific), packages, materials, location, process, site visit, contact, custom furniture, payment, edge cases, out-of-scope queries, and adversarial inputs.

---

## Roadmap

- [x] Knowledge base schema, loader, validation tests
- [x] OpenAI embedding pipeline + FAISS index (336 vectors)
- [x] Retrieval with similarity threshold and graceful fallback
- [x] Prompt engineering for strict formal-Bangla output
- [x] FastAPI webhook + Facebook Messenger integration
- [x] HMAC-SHA256 webhook signature verification
- [x] Human-takeover pause state (7-day sliding window)
- [x] Message classifier (emoji, sticker, attachment, URL, phone)
- [x] Echo detection for rep-reply via Page Inbox
- [x] Interactive CLI for exploratory testing
- [x] Catalog-based eval system (137 queries × 28 categories)
- [x] Docker multi-stage build, non-root user
- [x] Production deployment on DigitalOcean behind nginx + TLS
- [ ] `POST /admin/unpause` endpoint for per-PSID selective unpause
- [ ] SQLite persistence for pause state (survives container restarts)
- [ ] Facebook App Review — move out of Development Mode
- [ ] GitHub Actions CI/CD (auto build + push to GHCR on merge to main)
- [ ] Conversation memory (multi-turn context)
- [ ] Admin dashboard for live conversation monitoring

---

## License

This repository contains code only. The production knowledge base and any deployed assets remain proprietary to Minimal Limited.

Code is released under the MIT License — see `LICENSE`.

---

## Author

Built by **Md. Shihabuddin Sadi** — [sadishihab.github.io](https://sadishihab.github.io/)

A portfolio project demonstrating production RAG architecture, multilingual NLP for Bangla/Banglish/English, Facebook Messenger Platform integration, and containerised deployment on DigitalOcean.

The sample data in this repo is fictional and for demonstration only.
