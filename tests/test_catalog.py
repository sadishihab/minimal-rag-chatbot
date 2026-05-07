"""
Run all queries from tests/catalog.yaml through the Generator and
produce a markdown report for review.

Usage:
    python -m tests.test_catalog

Output:
    tests/catalog_report_YYYY-MM-DD_HH-MM.md
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


def escape_md(text: str) -> str:
    """
    Minimal markdown escaping for table cells.
    We only escape the characters that would break a table row:
    pipes (|) split cells, and newlines would split rows.
    """
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def run_category(generator: Generator, category: dict) -> list[dict]:
    """
    Run all queries in a category, return results for the report.
    Each result is a dict with: query, reply, time_ms, top_score, top_intent.
    """
    results = []
    for query in category["queries"]:
        start = time.perf_counter()

        # Call retriever separately so we can log scores into the report
        retrieval_results = generator.retriever.search(query)
        top_score = retrieval_results[0].score if retrieval_results else 0.0
        top_intent = retrieval_results[0].intent if retrieval_results else "-"

        try:
            reply = generator.generate(query)
            error = None
        except Exception as exc:
            reply = ""
            error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = (time.perf_counter() - start) * 1000

        results.append({
            "query": query,
            "reply": reply,
            "error": error,
            "time_ms": elapsed_ms,
            "top_score": top_score,
            "top_intent": top_intent,
        })

        # Progress dot to show something is happening
        print(".", end="", flush=True)

    return results


def write_report(all_results: list[dict], output_path: Path) -> None:
    """Write the markdown report."""
    lines: list[str] = []

    # Header
    lines.append(f"# Minimal Limited — Catalog Test Report")
    lines.append(f"")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"")

    # Summary
    total_queries = sum(len(c["results"]) for c in all_results)
    total_errors = sum(1 for c in all_results for r in c["results"] if r["error"])
    total_time = sum(r["time_ms"] for c in all_results for r in c["results"])

    lines.append(f"## Summary")
    lines.append(f"")
    lines.append(f"- **Total queries:** {total_queries}")
    lines.append(f"- **Errors:** {total_errors}")
    lines.append(f"- **Total time:** {total_time / 1000:.1f}s ({total_time / total_queries:.0f}ms avg)")
    lines.append(f"")

    # Table of contents — one link per category
    lines.append(f"## Categories")
    lines.append(f"")
    for cat in all_results:
        anchor = cat["category"].lower().replace("_", "-")
        lines.append(f"- [{cat['category']}](#{anchor}) — {len(cat['results'])} queries")
    lines.append(f"")

    # Per-category sections
    for cat in all_results:
        lines.append(f"## {cat['category']}")
        lines.append(f"")
        if cat.get("description"):
            lines.append(f"*{cat['description']}*")
            lines.append(f"")

        # Table header
        lines.append(f"| # | Query | Reply | Score | Intent | Time |")
        lines.append(f"|---|-------|-------|-------|--------|------|")

        for i, r in enumerate(cat["results"], start=1):
            query = escape_md(r["query"])
            if r["error"]:
                reply = f"🔴 ERROR: {escape_md(r['error'])}"
            else:
                reply = escape_md(r["reply"])
            score = f"{r['top_score']:.2f}"
            intent = r["top_intent"]
            time_ms = f"{r['time_ms']:.0f}ms"

            lines.append(f"| {i} | {query} | {reply} | {score} | {intent} | {time_ms} |")

        lines.append(f"")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    # Silence routine INFO logs; keep warnings/errors visible
    setup_logging(level="WARNING")

    # Load catalog
    if not CATALOG_PATH.exists():
        print(f"🔴 Catalog not found at {CATALOG_PATH}")
        return 1

    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    total_queries = sum(len(c["queries"]) for c in catalog)

    print("=" * 70)
    print(f"🧪 CATALOG RUNNER — {len(catalog)} categories, {total_queries} queries")
    print(f"   Estimated time: {total_queries * 3 // 60} min {total_queries * 3 % 60}s "
          f"(at ~3s/query)")
    print("=" * 70)

    # Initialize generator
    print("\nLoading generator...")
    generator = Generator()
    print()

    # Run every category
    all_results = []
    start = time.perf_counter()

    for cat in catalog:
        print(f"  {cat['category']:<30} ", end="", flush=True)
        cat_results = run_category(generator, cat)
        elapsed_cat = sum(r["time_ms"] for r in cat_results) / 1000
        print(f"  ({elapsed_cat:.1f}s)")

        all_results.append({
            "category": cat["category"],
            "description": cat.get("description", ""),
            "results": cat_results,
        })

    total_elapsed = time.perf_counter() - start

    # Write the report
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_path = REPORT_DIR / f"catalog_report_{timestamp}.md"
    write_report(all_results, report_path)

    print()
    print("=" * 70)
    print(f"✅ Done in {total_elapsed:.1f}s")
    print(f"   Report: {report_path}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())