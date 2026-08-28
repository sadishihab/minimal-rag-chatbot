"""
Run all queries from tests/catalog.yaml through retrieval (and optionally the
Generator) and produce a markdown report for review.

Usage:
    python -m tests.catalog_runner

Output:
    tests/catalog_report_YYYY-MM-DD_HH-MM.md
    exit code 1 if any case hard-FAILs, else 0

NOT a pytest suite, and deliberately not named like one. It needs a live
OPENAI_API_KEY, the proprietary knowledge base, and a built FAISS index —
none of which the pytest suites require. Keeping it out of `pytest tests/`
is what lets that suite run offline on any machine. It used to be called
test_catalog.py, from which pytest collected zero tests while reporting
success; see the "tests that report success while asserting nothing"
section in CLAUDE.md.

CATALOG SCHEMA
    - category: <name>                # required
      description: <text>             # optional, italic in the report
      generate: false                 # optional, category-level default
      queries:
        - "a bare string"             # legacy form, still supported
        - q: "apnara ki site visit koren?"    # required
          expect_intent: site_visit           # optional; without it nothing is checked
          min_score: 0.80                     # optional, applied only if intent matches
          xfail: true                         # known-failing: reported, never fatal
          watch: true                         # record only, never pass/fail
          generate: true                      # override the category default
          note: "why this case exists"        # optional, shown in the report

Generation is OFF by default. It is ~99.95% of the run cost and takes the
run from seconds to minutes, and most cases assert on the retrieved intent,
which does not need it. Turn it on where the reply text is the point.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from logger import setup_logging
from generation.generator import Generator


CATALOG_PATH = Path("tests/catalog.yaml")
REPORT_DIR = Path("tests")

# Generation off unless a category or case asks for it — see module docstring.
DEFAULT_GENERATE = False

# Statuses that mean "someone has to look at this".
HARD_FAIL = "FAIL"
STALE_XFAIL = "XPASS"


def escape_md(text: str) -> str:
    """
    Minimal markdown escaping for table cells.
    We only escape the characters that would break a table row:
    pipes (|) split cells, and newlines would split rows.
    """
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def normalize_case(item, category_generate: bool) -> dict:
    """
    Accept either a bare string (the original schema) or a mapping, and return
    a uniform dict. Keeping the string form working means a recovered older
    catalog still runs against this file.
    """
    if isinstance(item, str):
        item = {"q": item}
    if not isinstance(item, dict) or "q" not in item:
        raise ValueError(
            f"catalog case must be a string, or a mapping with a 'q' key: {item!r}"
        )
    return {
        "q": item["q"],
        "expect_intent": item.get("expect_intent"),
        "min_score": item.get("min_score"),
        "xfail": bool(item.get("xfail", False)),
        "watch": bool(item.get("watch", False)),
        "generate": bool(item.get("generate", category_generate)),
        "note": item.get("note", ""),
    }


def classify(case: dict, top_intent: str, top_score: float) -> str:
    """
    PASS / FAIL  — an expectation that held or did not
    XFAIL/XPASS  — a known-failing case that still fails, or has started passing
    WATCH        — recorded only; a thin margin we want visible, not enforced
    —            — no expectation was declared
    """
    if case["watch"]:
        return "WATCH"
    if case["expect_intent"] is None:
        return "—"

    ok = top_intent == case["expect_intent"]
    if ok and case["min_score"] is not None:
        ok = top_score >= case["min_score"]

    if case["xfail"]:
        # XPASS is not fatal, but it means the KB was fixed and this entry is
        # now lying about the state of the world. Someone should clear the flag.
        return "XPASS" if ok else "XFAIL"
    return "PASS" if ok else "FAIL"


def run_category(generator: Generator, category: dict) -> list[dict]:
    """
    Run all queries in a category, return results for the report.
    """
    category_generate = bool(category.get("generate", DEFAULT_GENERATE))
    results = []

    for item in category["queries"]:
        case = normalize_case(item, category_generate)
        start = time.perf_counter()

        # Retrieval is always run — it is what almost every case asserts on,
        # and it is ~1/2000th the cost of a generation call.
        retrieval_results = generator.retriever.search(case["q"])
        top_score = retrieval_results[0].score if retrieval_results else 0.0
        top_intent = retrieval_results[0].intent if retrieval_results else "-"

        reply, error = "", None
        if case["generate"]:
            try:
                reply = generator.generate(case["q"])
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = classify(case, top_intent, top_score)

        results.append({
            "query": case["q"],
            "expect_intent": case["expect_intent"],
            "min_score": case["min_score"],
            "note": case["note"],
            "generated": case["generate"],
            "reply": reply,
            "error": error,
            "time_ms": elapsed_ms,
            "top_score": top_score,
            "top_intent": top_intent,
            "status": status,
        })

        print("." if status not in (HARD_FAIL,) else "F", end="", flush=True)

    return results


def write_report(all_results: list[dict], output_path: Path) -> None:
    """Write the markdown report."""
    lines: list[str] = []
    every = [r for c in all_results for r in c["results"]]

    def count(s):
        return sum(1 for r in every if r["status"] == s)

    lines.append("# Minimal Limited — Catalog Test Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    total_queries = len(every)
    total_errors = sum(1 for r in every if r["error"])
    total_time = sum(r["time_ms"] for r in every)
    generated = sum(1 for r in every if r["generated"])

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total queries:** {total_queries} "
                 f"({generated} with generation, {total_queries - generated} retrieval-only)")
    lines.append(f"- **PASS:** {count('PASS')}")
    lines.append(f"- **FAIL:** {count('FAIL')}")
    lines.append(f"- **XFAIL** (known-failing, expected): {count('XFAIL')}")
    lines.append(f"- **XPASS** (known-failing but now passing — clear the flag): {count('XPASS')}")
    lines.append(f"- **WATCH** (recorded, not enforced): {count('WATCH')}")
    lines.append(f"- **Errors:** {total_errors}")
    lines.append(f"- **Total time:** {total_time / 1000:.1f}s "
                 f"({total_time / total_queries:.0f}ms avg)" if total_queries else "")
    lines.append("")

    lines.append("## Categories")
    lines.append("")
    for cat in all_results:
        anchor = cat["category"].lower().replace("_", "-")
        n_fail = sum(1 for r in cat["results"] if r["status"] == "FAIL")
        flag = f" — **{n_fail} FAIL**" if n_fail else ""
        lines.append(f"- [{cat['category']}](#{anchor}) — {len(cat['results'])} queries{flag}")
    lines.append("")

    for cat in all_results:
        lines.append(f"## {cat['category']}")
        lines.append("")
        if cat.get("description"):
            lines.append(f"*{cat['description']}*")
            lines.append("")

        lines.append("| # | Query | Expected | Score | Intent | Status | Note | Reply | Time |")
        lines.append("|---|-------|----------|-------|--------|--------|------|-------|------|")

        for i, r in enumerate(cat["results"], start=1):
            expected = r["expect_intent"] or "—"
            if r["min_score"] is not None:
                expected += f" ≥{r['min_score']:.2f}"
            if r["error"]:
                reply = f"🔴 ERROR: {escape_md(r['error'])}"
            elif not r["generated"]:
                reply = "*(not generated)*"
            else:
                reply = escape_md(r["reply"])
            lines.append(
                f"| {i} | {escape_md(r['query'])} | {expected} | {r['top_score']:.3f} "
                f"| {r['top_intent']} | {r['status']} | {escape_md(r['note'])} "
                f"| {reply} | {r['time_ms']:.0f}ms |"
            )

        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    # Silence routine INFO logs; keep warnings/errors visible
    setup_logging(level="WARNING")

    if not CATALOG_PATH.exists():
        print(f"🔴 Catalog not found at {CATALOG_PATH}")
        return 1

    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    total_queries = sum(len(c["queries"]) for c in catalog)
    to_generate = sum(
        1
        for c in catalog
        for item in c["queries"]
        if normalize_case(item, bool(c.get("generate", DEFAULT_GENERATE)))["generate"]
    )

    print("=" * 70)
    print(f"🧪 CATALOG RUNNER — {len(catalog)} categories, {total_queries} queries")
    print(f"   {to_generate} with generation, {total_queries - to_generate} retrieval-only")
    print("=" * 70)

    print("\nLoading generator...")
    generator = Generator()
    print()

    all_results = []
    start = time.perf_counter()

    for cat in catalog:
        print(f"  {cat['category']:<32} ", end="", flush=True)
        cat_results = run_category(generator, cat)
        elapsed_cat = sum(r["time_ms"] for r in cat_results) / 1000
        print(f"  ({elapsed_cat:.1f}s)")

        all_results.append({
            "category": cat["category"],
            "description": cat.get("description", ""),
            "results": cat_results,
        })

    total_elapsed = time.perf_counter() - start

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_path = REPORT_DIR / f"catalog_report_{timestamp}.md"
    write_report(all_results, report_path)

    every = [r for c in all_results for r in c["results"]]
    hard_fails = [r for r in every if r["status"] == HARD_FAIL]
    stale = [r for r in every if r["status"] == STALE_XFAIL]

    print()
    print("=" * 70)
    print(f"   PASS {sum(1 for r in every if r['status']=='PASS')}"
          f"   FAIL {len(hard_fails)}"
          f"   XFAIL {sum(1 for r in every if r['status']=='XFAIL')}"
          f"   XPASS {len(stale)}"
          f"   WATCH {sum(1 for r in every if r['status']=='WATCH')}")
    for r in hard_fails:
        print(f"   🔴 FAIL  {r['query']!r} -> {r['top_intent']} @ {r['top_score']:.3f} "
              f"(expected {r['expect_intent']})")
    for r in stale:
        print(f"   ⚠️  XPASS {r['query']!r} now passes — clear its xfail flag")
    print(f"✅ Done in {total_elapsed:.1f}s")
    print(f"   Report: {report_path}")
    print("=" * 70)

    return 1 if hard_fails else 0


if __name__ == "__main__":
    sys.exit(main())
