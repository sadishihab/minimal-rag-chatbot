# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hand-rolled RAG chatbot for **Minimal Limited**, an interior design firm in Dhaka, Bangladesh, serving customers over Facebook Messenger. Customers write in Bangla, Banglish, or English (often mixed within one message); the bot always replies in formal Bangla. No LangChain/LlamaIndex/ChromaDB — every stage of the pipeline (embed → FAISS search → prompt build → OpenAI call → post-process) is plain Python so the data flow stays inspectable. See README.md for the full architecture diagram and design rationale ("Key Design Decisions" section) — read it before changing retrieval thresholds, the language-triplication scheme, or the pause system.

Status: **live in production and serving real customers**, published on the Minimal Limited Facebook Page, currently 23:00–09:00 Asia/Dhaka only. A separate test environment runs alongside it. See *Deployment and environments* below.

Because production is live, assume any change you make can reach a real customer. The current work is knowledge-base quality driven by real customer queries, not new features.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in OPENAI_API_KEY + Facebook creds
cp data/knowledge_base.sample.json data/knowledge_base.json   # real KB is gitignored/proprietary

# Build the FAISS index (required before running the API or tests that hit the retriever)
python -m ingestion.indexer

# Run locally
python -m tests.chat_cli                          # interactive CLI, no FastAPI/Messenger needed
uvicorn api.server:app --reload --port 8000        # full server (POST /chat, GET /health, /webhook)

# Tests
pytest tests/ -v                                   # test_api.py errors at collection — see Known issues
PYTHONPATH=. python tests/test_message_classifier.py  # script-style, NOT collected by pytest
pytest tests/test_active_hours.py::test_name -v    # single test

# KB retrieval eval (50 cases / 4 categories, reads tests/catalog.yaml).
# Hits the real OpenAI API — but generation is off by default, so a full run
# is ~$0.006 and ~47s. Exits 1 on any hard FAIL. NOT a pytest suite.
python -m tests.catalog_runner
# → writes tests/catalog_report_YYYY-MM-DD_HH-MM.md (gitignored)

# KB hygiene check
python -m tests.audit_newlines

# Lint (ruff is used — see .ruff_cache; no committed ruff.toml, defaults apply)
ruff check .
```

`main.py` at the repo root is an unused PyCharm stub, not the entry point — the real app is `api.server:app`.

## Architecture

Request flow (Messenger path — `api/messenger.py` → `generation/generator.py`):

1. **`api/messenger.py`** — Messenger webhook (`/webhook`). Verifies the FB HMAC-SHA256 signature, then applies the **active-hours gate** (`api/active_hours.py`): the bot is active when it is inside `BOT_ACTIVE_START_HOUR`..`BOT_ACTIVE_END_HOUR` (default 23→9, always Asia/Dhaka, wraps midnight) **OR** on a weekday listed in `BOT_ALWAYS_ACTIVE_DAYS` (default empty; production is `friday`, making Thu 23:00 → Sat 09:00 one continuous 34-hour stretch). Otherwise it acks FB with 200 and does nothing else — no sends, no `pause_state` reads or writes, no RAG. The gate sits above the `entry[].messaging[]` loop so it covers every event type including echoes and postbacks; do not move it below that loop or into `process_messaging_event`. `validate_window()` rejects out-of-range and zero-length windows at import, and `validate_days()` rejects unknown day names the same way — `0`/`24` is the explicit always-active config. Both fail at import so a typo stops the container booting rather than silently meaning "no always-active days". When active, each event is routed *before* it ever reaches the RAG pipeline:
   - `is_echo` message where `app_id != FACEBOOK_APP_ID` → a human rep replied via Page Inbox → `pause_state.pause_thread()` (bot goes silent for that customer for 7 days, sliding window)
   - thread already paused → bot stays silent for text; attachments still get an acknowledgment
   - attachment that's all stickers → "ধন্যবাদ" only, no pause
   - any other attachment, or text containing a URL → handoff message + pause (needs human review)
   - emoji-only text → "ধন্যবাদ" only, no pause, no RAG call
   - text that is *entirely* one of a closed list of acknowledgements ("ok",
     "thanks", "আচ্ছা", "ঠিক আছে", …) → "ধন্যবাদ" only, no pause, no RAG call.
     **Whole-message match only** — "ok koto lagbe?" is a question and
     "ok 01775760496" is a shared number, so both fall through. Adding a
     variant to the list is a maintainer decision, pinned by
     `test_the_literal_list_is_exactly_what_was_agreed`.
     The single relaxation is a run of emoji at **either end**: "ok 👍" and
     "👍 ok" match; "ok 👍 koto lagbe?" does not (mid-message is not an edge,
     and the text is still a question), and neither does "ok bhai" — emoji are
     treated as punctuation-like, a word never is. Honorifics were proposed
     twice and refused twice: they have no natural end as a list. Stripping
     reuses `_is_emoji_char`, so this branch and the emoji-only one cannot
     disagree about what an emoji is. It sits **below** emoji-only for that
     reason: a bare "👍" belongs to that branch, and `is_acknowledgement`
     declines it independently since stripping leaves nothing.
     Note when mutation-testing this: "strip emoji anywhere" is **not** caught
     by "ok 👍 koto lagbe?" — that reduces to "ok koto lagbe?", still not on
     the list, still False either way. Only mid-*word* cases ("o👍k") separate
     edge-stripping from anywhere-stripping.
   - otherwise → falls through to `generator.generate(text)`

   Above all of those branches (and below the echo branch), any inbound text
   containing a phone number sets `phone_shared_state.mark_phone_shared()`.
   It has to sit there because a number can arrive on any branch — with a URL,
   as an image caption, or into a paused thread — and only the last branch
   reaches the generator's own detector. Do not move it below the pause check:
   a number given while a rep owns the thread still has to be remembered,
   because the pause expires and the bot comes back.
2. **`generation/generator.py`** (`Generator` class) — the actual RAG pipeline, also reachable directly via `POST /chat`:
   - sanitize input (`generation/sanitizer.py`)
   - phone-number bypass (`generation/phone_detector.py`) — if the customer shares a number, skip retrieval/LLM entirely and send a canned acknowledgment
   - retrieve (`retrieval/retriever.py`): embed the query, FAISS `IndexFlatIP` cosine search, `top_k=4`, drop anything below `SIMILARITY_THRESHOLD=0.3`
   - build prompt (`generation/prompt_builder.py`) with strict formal-Bangla output rules
   - call `gpt-4o-mini`, with per-exception-type OpenAI error handling, each mapped to a specific Bangla fallback message (rate limit / connection / auth / bad request / generic) — `Generator.generate()` is designed to never raise, always return a user-facing string
   - post-process: `generation/formatter.py` strips markdown that leaked through
3. **`api/messenger.py:send_reply()`** is the send boundary, and every send in
   `process_messaging_event` goes through it — not just the RAG reply. If that
   customer has already shared a phone number, it swaps `HANDOFF_MESSAGE` for
   `HANDOFF_MESSAGE_PHONE_SHARED` and runs `cta_substitution.substitute_cta()`
   over the text, then `check_for_drift()`. **This is the only point in the
   process that holds both a reply and a PSID**, which is why it lives here
   rather than in `Generator.generate()` — `generate()` takes a bare string and
   stays identity-free by design. Do not thread a PSID into it.
4. **`api/send_api.py`** sends the reply back via the Messenger Send API.

Ingestion is a separate, one-time-per-KB-change pipeline (`ingestion/loader.py` → `ingestion/embedder.py` → `ingestion/indexer.py`), run manually via `python -m ingestion.indexer`. It is not invoked at request time — `Retriever` just loads the pre-built `vector_store/faiss.index` + `vector_store/metadata.json` at startup and reuses them for every query. The Docker `entrypoint.sh` builds the index automatically on container start if it's missing.

**Knowledge base language scheme**: every Q&A is stored three times (Bangla, Banglish, English versions of the *question*, all pointing to the same Bangla *answer*), so embedding search matches regardless of which script/language the customer typed in. The KB is embedded on the question, never the answer — answers are retrieved as metadata, not searched. This is why KB edits should always add/edit in triplets, not single entries, unless deliberately doing something else.

**Pause state** (`api/pause_state.py`) is an in-memory process-local dict — lost on restart by design for now (self-healing: rep's next reply re-pauses). Do not add persistence without checking the README roadmap; SQLite-backed persistence is a known open item.

**The justification for that has weakened, and the doc used to overstate it.** It read "the bot effectively starts fresh each night, so a 7-day pause never needs to survive a restart" — true of a 10-hour window, false since `BOT_ALWAYS_ACTIVE_DAYS=friday`. The bot now runs Thu 23:00 → Sat 09:00 as one 34-hour stretch, so a pause set by a rep on Thursday night has to survive far longer, and the self-healing story depends on a rep replying again. **No rep works Friday** — that is the entire reason the day is covered — so a mid-stretch restart silently un-pauses a thread a rep had taken over, the bot resumes talking to that customer, and nobody notices until Saturday. Weigh that before restarting production mid-stretch, and treat SQLite persistence as closer to necessary than the roadmap entry implies.

**Phone-number CTA substitution** (`api/cta_substitution.py` +
`api/phone_shared_state.py`). Most KB answers close by asking the customer to
share their mobile number. Once they have, that line reads as the bot having
lost the conversation — and it repeats on every later reply. `send_reply()`
strips it for flagged customers.

Three things about it are load-bearing:

- **Replacement, never truncation.** The ask sits mid-answer in 70 of its 78
  full-form occurrences, with URLs after it in 45. Cutting from the CTA onward
  destroys those links.
- **Ordered longest-first.** `CTA_SHORT` is a *substring* of `CTA_FULL`.
  Matching short first rewrites only the tail and strands the lead-in, giving a
  doubled anaphor (`...বিস্তারিত তথ্যের জন্য এ বিষয়ে...`). `_SUBSTITUTIONS`
  sorts by pattern length rather than being hand-ordered; the ordering is
  pinned by `test_substring_ordering_does_not_corrupt_the_full_cta`.
- **`site_visit` has its own replacement.** The generic one drops the promise
  to schedule the visit — a content regression, not a wording change.

The CTA constants are copied verbatim out of the gitignored KB. A hand-edit to
any of the 93 answers that carry one silently drops it out of coverage, so
`tests/audit_cta_variants.py` fails if the KB drifts away from the constants.
It skips (does not fail) when the real KB is absent.

`phone_shared_state` mirrors `pause_state` exactly — same shape, same
in-memory-and-lost-on-restart tradeoff, no expiry. It is the **second**
consumer of the missing persistence, which strengthens the SQLite case above
rather than adding a new problem.

**Config** (`config.py`) is the single source of truth for paths, model names (`gpt-4o-mini`, `text-embedding-3-small`), `TOP_K`, `SIMILARITY_THRESHOLD`, `BOT_ACTIVE_START_HOUR`/`BOT_ACTIVE_END_HOUR`/`BOT_ALWAYS_ACTIVE_DAYS`, and input/body-size limits — check here before hardcoding any of those values elsewhere.

`api/server.py` instantiates one `Generator` at FastAPI startup (`app.state.generator`) and reuses it for every request/webhook event — never construct a new `Generator` (or `Retriever`) per-request, it reloads the FAISS index each time.

## Data

- `data/knowledge_base.json` — the real KB, gitignored and proprietary (339 entries in production). Never commit real customer/business data here.
- `data/knowledge_base.sample.json` — fictional 8-entry sample showing the schema (`data/README.md` documents required fields: `id`, `intent`, `sub_intent`, `language`, `question`, `answer`, `attachments`). Use this for any example/test KB content.
- `vector_store/` (FAISS index + metadata) is gitignored and regenerated locally via the indexer — never hand-edit it.

## Deployment and environments

Two Meta apps, one per environment. This is forced by the platform, not a preference: **each Meta app has exactly one webhook URL**, so test and production cannot share an app and still point at different code.

|             | Test                          | Production                    |
|-------------|-------------------------------|-------------------------------|
| Meta app    | existing (`1484659980123174`) | separate app, client-owned    |
| Page        | private test page             | Minimal Limited               |
| Domain      | `staging.minimallimited.com`  | `chat.minimallimited.com`     |
| Container   | `minimal-rag-test`            | `minimal-rag`                 |
| Port        | 8001                          | 8000                          |
| Env file    | `~/.env.test`                 | `~/.env.prod`                 |
| Image tag   | `:latest`                     | `:vX.Y.Z` — **pinned**        |
| Window      | `0 → 24`                      | `23 → 9`                      |
| Always-active days | *(none)*               | `friday`                      |
| Status      | Development Mode              | **Published, real customers** |

Host: DigitalOcean, Ubuntu 24.04, Singapore, 2GB / 1 vCPU. nginx terminates TLS and proxies by subdomain. Secrets are injected at `docker run` time via `--env-file`, never baked into the image and never passed as `-e` flags (which would land in shell history).

**Production must never run `:latest`.** Tag every build twice — `:latest` and `:vX.Y.Z`. Test pulls `:latest`; production pulls the explicit version, and only after testers sign off. Without this, any stray `docker push` silently lands on the client's live customer channel. Rollback is then just re-running the previous tag. Current production release is `v0.6.0`.

Note that `data/knowledge_base.json` is `COPY`d into the image at build time, so **a version tag pins code and knowledge base together**. That is deliberate — it is what makes a KB-caused regression rollback-able, and it matters more now that KB edits are the main ongoing work.

**Deploy only while the bot is inert — and derive that from the configured schedule, not from fixed hours.** The safe window is the complement of the gate: outside `BOT_ACTIVE_START_HOUR`..`BOT_ACTIVE_END_HOUR` **and** not on a `BOT_ALWAYS_ACTIVE_DAYS` weekday, Asia/Dhaka. Read the values actually set in `~/.env.prod` before deploying rather than assuming the defaults; `describe_schedule()` renders the rule set the running container is applying, and it appears in every `Outside active hours (...)` log line, so `docker logs minimal-rag | grep 'Outside active hours'` tells you what production really thinks its schedule is.

Restarting production clears all in-memory pause state, so any restart while the bot is active cancels every rep handoff currently in force.

With the intended production config (`23`→`9` plus `BOT_ALWAYS_ACTIVE_DAYS=friday`) that means 09:00–23:00 Dhaka, Saturday through Thursday — and **there is no safe deploy window on a Friday at all.** The bot runs Thu 23:00 → Sat 09:00 as one continuous 34-hour stretch with no inert gap anywhere inside it. A restart in that stretch cancels every active handoff on the one day nobody is at Page Inbox, so the bot resumes talking to customers a rep had taken over and it goes unnoticed until Saturday morning.

If a Friday deploy is genuinely unavoidable, note that the emergency stop below does **not** rescue this: it stops new traffic reaching the bot, but the pause state is lost either way and the cancelled handoffs resurface the moment the app is switched back on. Treat a Friday deploy as accepting that cost and tell the client, or wait until Saturday 09:00.

**App Review is not required** — settled by evidence, not assumption. Meta allowed the app to be published without review, and on the first live night real customers holding no app, developer or tester role received replies. Standard Access is sufficient for a Direct Developer using the API for their own Page. The known side effect is that customer names appear as "John Doe" without advanced `pages_messaging`, which is irrelevant here because the bot never uses names. Revisit only if a Send API permission error appears in the logs.

**No in-conversation bot disclosure**, deliberately. Meta requires it where applicable law does (California and Germany are the flagged jurisdictions) and recommends it elsewhere; Bangladesh has no such law. The App Review angle that would have argued for it is now moot.

**Emergency stop:** set the app status to OFF in the Meta dashboard. That reverts it to Development Mode instantly, so only app roles can reach the bot — faster than stopping the container, and it leaves the infrastructure untouched.

## Commit conventions

**You commit autonomously.** Do not ask for permission before committing. When
a unit of work is complete and the gates below pass, commit it, push it, and
report what you did.

### Hard gates — never commit if any of these fail

1. **The full test suite passes.** Run every suite, not just the one you
   touched. Some suites print PASS/FAIL rather than exiting non-zero, so
   **read the output** rather than trusting the exit code.

   ```bash
   pytest tests/ -v
   PYTHONPATH=. python tests/test_message_classifier.py
   ```

   `tests/test_api.py` errors at collection (see *Known issues*) — that is
   pre-existing and expected, not a regression you introduced. Everything else
   must be green.

2. **Any project-specific validation passes** if you touched config, schema, or
   manifest files.

3. **The diff contains only what you intended.** Run `git status --short` and
   `git diff --stat` before staging. If a file you did not mean to touch
   appears, stop and report it.

4. **No secrets, binaries, or build artifacts.** Never stage anything under
   `dist/`, `build/`, `__pycache__/`, `*.egg-info/`, `node_modules/`, or any
   file containing a token or key.

### What goes in a commit

- **One logical change.** Never mix a refactor with a behaviour change, or
  formatting with a feature.
- **Behaviour changes land with their test, in the same commit.** A fix without
  a test that would have caught it is not finished.
- **Mechanical changes get their own commit**, clearly labelled (e.g.
  `chore: ruff --fix`).
- **Config and tooling changes are separate** from product changes.

### Commit messages

Subject under 72 characters with a conventional prefix (`feat:`, `fix:`,
`chore:`, `docs:`, `test:`, `build:`).

Then a body explaining **why**, not what — the diff shows what. What was wrong
before, why this approach over the alternatives, and anything a future reader
would otherwise have to rediscover. If you made a judgement call, say what you
decided and why.

### After committing

Push, then report in this exact form:

```
committed <sha> <subject>
  <file>  +N -M
  <file>  +N -M
tests: <suites run, result>
pushed to origin/main
```

### Stop and ask instead of proceeding

Commit freely, but surface it when:

- A test fails, or you had to change a test to make it pass.
- The change touches anything user-facing: copy, error messages, documentation
  a user reads, licence or privacy text. **All customer-facing Bangla copy is
  owned by the maintainer, not by you** — propose wording, do not ship it.
- You are about to delete or rename a file, or move code between files.
- You would need `--force`, or to amend or rebase anything already pushed.
- The task turned out to require a design decision that was not specified.

### Never, under any circumstances

- Force push, rewrite pushed history, or delete branches or tags.
- Use `git add -A` or `git add .` — stage only your own files by explicit path.
  The working tree often contains unrelated uncommitted work.
- Version-bump anything without being asked.

---

## Verification conventions

Design checks that could actually fail. A green run proves nothing unless the
same check would go red if the thing were broken.

- **Every new guard needs a positive control.** A suite where nothing is ever
  rejected, cached, or substituted will pass every negative test.
- **Remove the dependency and re-run.** Move the gitignored file aside, use
  `--no-cache-dir`, unset the env var, try a fresh clone. Things that work only
  on this machine look identical to things that work.
- **Read what "passed" means.** A test can print success while asserting
  nothing.
- **After a scripted edit, check the diffstat.** Zero lines means the file was
  untracked; hundreds means a rewrite reformatted everything.
- **After any file move, grep for stale references** in READMEs, docs,
  docstrings, and CI config.
- **When a tool reports success but nothing changed, believe the state, not the
  report.**

---

## Project-specific traps

Each of these cost real time once. Add to the list rather than remembering.

**A stale image can be deployed while the pull appears to succeed.**
GHCR authentication is per-machine — logging in locally does not log in the
droplet. An unauthenticated `docker pull` fails with
`error from registry: denied`, but a subsequent `docker run` happily starts the
*previously cached* image. The deploy looks like it worked and the old code
keeps running. Always check the digest reported by `docker pull` against the one
`docker push` printed, and `docker login ghcr.io` on every machine that pulls.

**Retrieval clustering silently answers from the wrong intent.**
Semantically adjacent intents sit close in embedding space, so a query can land
nearer the wrong cluster and produce a confident, fluent, wrong answer — e.g. a
location question retrieving `services_offered`. The symptom looks like a
generation/hallucination bug and is not. **Check the logged top score and
matched intent before touching the prompt.** The fix is a canonical KB triplet
whose question matches the failing phrasing closely.

**Webhook fields must be subscribed in two separate places, and missing the
second produces total silence.**
App level (Messenger API Settings → section 1 → Webhook fields) *and* page level
(section 2 → the page row's own **Add Subscriptions** button). Ticking the app-level
fields does NOT subscribe the page; until you use that button the row reads
"No fields subscribed."

The symptom is nothing at all: no POST in the container log, no POST in the nginx
access log, no error anywhere, nothing under "Show Recent Errors". Webhook
verification passes, containers are healthy, the token is valid, the page shows as
connected, and every app-level field is green. Facebook simply never attempts
delivery. This cost a full debugging cycle on the production page.

Diagnosis: `sudo grep webhook /var/log/nginx/access.log | tail -20`. If it shows
the verification GETs but no POSTs, the event never left Meta — which rules out
tokens, signatures, and the active-hours gate, all of which produce log output.

**The CTA substitution is invisible from `/chat` and the CLI — by design.**
It runs in `send_reply()`, which only the Messenger path calls. `POST /chat`
and `python -m tests.chat_cli` have no PSID, so they will always show the
original "share your mobile number" closing no matter what state the flag is
in. Anyone QA-ing this feature through the CLI will conclude it is broken.
It has to be tested through Messenger, or through
`tests/test_phone_shared_flow.py`.

The same is true of every branch that sits **above** `generate()` in
`process_messaging_event` — phone-shared marking, the CTA substitution, and
now the acknowledgement branch (`is_acknowledgement`). "Ok" typed into
`python -m tests.chat_cli` or `POST /chat` still produces a full RAG answer
and always will, because neither path goes through `api/messenger.py`. The
CLI is the wrong instrument for anything in that layer.

**`grep 'CTA drift'` is the health check for the substitution.**
The reply is model output, not raw KB text, so exact-string matching only works
as far as `gpt-4o-mini` reproduces the KB verbatim — and the prompt's own
CLOSING RULES section calls that "the most-violated rule". Every WARNING is a
case where the model reworded the CTA and a flagged customer got asked for
their number anyway, with the reply text attached so the wording is
recoverable. Quiet log ⇒ exact matching is sufficient. Noisy log ⇒ the fix is
prompt tightening or fuzzy matching, not a wider regex guessed at in advance.

**A paused thread makes every subsequent test look broken.**
Once `pause_state` holds a PSID, everything from that customer returns silence in
~3ms, including tests of completely unrelated branches. A test that "fails" with
`text during paused thread → bot SILENT` is showing you the pause, not the feature
under test. Restart the container to clear pause state before testing anything else.

**Graph API Explorer injects the deprecated `manage_pages` scope.**
Generating a page token there fails with `Invalid Scopes: manage_pages` no matter
what the permissions list shows — the Explorer carries it internally. Don't use it
for page tokens. The dashboard token (Messenger API Settings → section 2 →
Generate) is better anyway: it never expires, while the Explorer's is short-lived
and needs a long-lived exchange. `pages_manage_metadata` is not needed either — it
would only let you read the subscription list back, and verifying echo behaviour
directly (rep replies from Page Inbox, bot goes silent) is the better check.

**`certbot --nginx` can write a server block that drops every connection.**
On a new subdomain it issued the certificate correctly, then appended a server
block copied from the nearest template it could find — the catch-all — so the new
block contained `return 444`. Valid TLS, every connection dropped. It did not
damage the existing blocks. After any `certbot --nginx` run, read the block it
wrote rather than assuming it proxies anywhere.

**A fresh clone cannot run.**
Both `data/knowledge_base.json` and `vector_store/` are gitignored, so the repo
alone is not enough. Recover the KB from a running container:
`docker exec minimal-rag cat /app/data/knowledge_base.json > data/knowledge_base.json`,
then rebuild the index. Anything claiming to verify a clean-checkout path must
account for this.

**Container log timestamps are UTC; the gate is Dhaka.**
The container clock runs UTC (+0) while `active_hours` pins Asia/Dhaka (+6). A
log line reading `13:27` is `19:27` local. Do not read log times as local when
reasoning about whether the gate should have fired — that mismatch is exactly
what the `ZoneInfo` pinning exists to survive.

**`tzdata` is present in `python:3.13-slim`** — verified directly against the
base image, not assumed. If the base image ever changes,
`ZoneInfo("Asia/Dhaka")` raises `ZoneInfoNotFoundError` at import: a boot crash
that appears only in Docker and never locally. One-line fix if it happens — add
`tzdata` to `requirements.txt`, which `zoneinfo` falls back to automatically.

## Known issues (filed, not in scope of current work)

### Tests that report success while asserting nothing

Three open instances, plus one closed. This is the failure mode the
*Verification conventions* section above exists to prevent, so treat a new
green suite as unproven until you have watched it go red.

0. **CLOSED — `tests/test_catalog.py` was named like a suite, collected zero
   tests under pytest, and contained no assertions at all.** Renamed to
   `tests/catalog_runner.py`, which no longer claims to be a test, and given
   real pass/fail semantics with a non-zero exit on hard failures. It was
   renamed rather than given a `test_*` entry point on purpose: it needs a
   live `OPENAI_API_KEY`, the proprietary KB, and a built FAISS index, and
   putting that inside `pytest tests/` would cost money on every run and
   break the suite's ability to run offline anywhere.

1. **`tests/test_message_classifier.py` collects zero tests under pytest.** Its
   functions are named `run_emoji_tests`/`run_sticker_tests` and only execute
   under `if __name__ == "__main__"`, so pytest finds nothing to run and
   reports success. Run it as
   `PYTHONPATH=. python tests/test_message_classifier.py` — the prefix is
   required: run directly, the repo root is not on `sys.path` and the import
   of `api.message_classifier` fails with `ModuleNotFoundError`. Under pytest
   that never shows, because pytest inserts the rootdir itself.
2. **`tests/test_api.py::test_health` returns `False` and pytest still reports
   it as passed.** It `return`s a bool instead of asserting, so the result is
   discarded; pytest only warns (`PytestReturnNotNoneWarning`). It reports
   passed with no server running at all. Same class of bug as instance 1.
3. **`test_bot_is_active_ignores_host_timezone` was vacuous** — it asserted
   `bot_is_active() is is_within_active_hours(now_in_dhaka())`, which is that
   function's own definition, so both sides moved together under any clock
   change. Fixed in 70e6539 by freezing the clock; kept here as the worked
   example of how the shape hides.

### Other

- `tests/test_api.py` also errors at collection for `test_chat` and
  `test_validation_error` (`fixture 'label' not found`) — it is a script-style
  harness meant for `python -m tests.test_api` against a live server.
- `import re` sits mid-file in `api/messenger.py` rather than at the top.
- Postbacks have no branch of their own in `process_messaging_event`; they fall
  through to the "no text and no attachments" handoff.
- **Greeting coverage in the KB is thin.** "Hi" retrieves at `0.322` against a
  `0.30` threshold — an uncomfortable margin on the single most common opening
  message. Needs more greeting triplets, not a threshold change.

### Watch out for

- `is_within_active_hours()` converts any **aware** datetime to Asia/Dhaka
  before comparing, which means it silently repairs a clock that returns
  aware host-local time. A bug in `now_in_dhaka()` of that shape is invisible
  in `bot_is_active()`'s return value and only shows at the clock source —
  which is why `test_bot_is_active_ignores_host_timezone` asserts on the
  returned `utcoffset()` as well as on the boolean.

- **The same trap applies to the weekday, and the OR makes it worse.**
  `is_always_active_day()` goes through the same `_to_dhaka()` helper, so it
  inherits that silent repair. On top of it, the `or` in `is_bot_active_at()`
  actively hides a wrong weekday: UTC and Dhaka disagree about the day during
  exactly Dhaka 00:00–05:59, and that region sits **entirely inside** the
  23→9 window, so `is_within_active_hours()` already returns `True` for every
  instant a naive `dt.weekday()` would get wrong. A test using the production
  window passes identically whether the weekday is read before or after
  conversion. `test_weekday_is_read_in_dhaka_not_utc` therefore asserts on the
  day predicate **alone**, and `test_combined_predicate_reads_the_weekday_in_dhaka`
  uses a **9→17** window where the day rule alone decides. Do not "tidy" that
  second test to use the production values — it guts it.

- **`WEEKDAY_NAMES` is a hardcoded tuple, not `calendar.day_name` or
  `strftime("%A")`.** Both of those are locale-dependent: under a non-English
  `LC_TIME` they would fail to match `"friday"` and read a valid config as
  "no always-active days".
