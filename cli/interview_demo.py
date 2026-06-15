#!/usr/bin/env python3
"""One-command DAT interview demo: smoke test + headline stats + file paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
WORKSPACE = BACKEND_DIR / "workspace"


def _run_smoke(*, check_links: bool, popularity: bool) -> int:
    cmd = [sys.executable, str(REPO_ROOT / "cli" / "smoke_test_tools.py")]
    if check_links:
        cmd.append("--check-links")
    if popularity:
        cmd.extend(["--with-popularity", "--popularity-limit", "50"])
    return subprocess.call(cmd, cwd=str(BACKEND_DIR))


def _print_headlines() -> None:
    gaps_path = WORKSPACE / "data" / "gaps.json"
    if not gaps_path.exists():
        print("No gaps.json — run smoke test first.")
        return

    result = json.loads(gaps_path.read_text(encoding="utf-8"))
    gap_class = result.get("gap_class_summary") or {}
    cat_total = result.get("category_count_total", 0)
    dot_count = result.get("dot_dataset_count", 0)

    print("\n" + "=" * 60)
    print("DAT INTERVIEW — HEADLINE STATS")
    print("=" * 60)
    print(f"  DOT portal datasets:        {dot_count}")
    print(f"  Category assignments:       {cat_total} (one primary_category each)")
    print(f"  True gaps (mirror-worthy):  {gap_class.get('true_gap', 0)}")
    print(f"  Intentionally external:     {gap_class.get('intentional_external', 0)}")
    print(f"  Total issues flagged:       {result.get('gap_count', 0)}")

    missing = [g for g in result.get("gaps", []) if g.get("status") == "missing_on_portal"]
    true_gaps = [g for g in missing if g.get("gap_class") == "true_gap"]
    if true_gaps:
        print("\n  Top 5 true gaps (priority_score):")
        for gap in true_gaps[:5]:
            print(
                f"    [{gap.get('priority_score', 0)}] {gap.get('title')} "
                f"— {gap.get('recommended_action', '')}"
            )

    print("\n  OPEN THESE FILES:")
    print(f"    gaps.csv:   {WORKSPACE / 'data' / 'gaps.csv'}")
    print(f"    report:     {WORKSPACE / 'reports' / 'gap_analysis_report.md'}")
    print(f"    script:     {REPO_ROOT / 'docs' / 'INTERVIEW_DEMO.md'}")
    print("=" * 60)
    print(
        '\nTalking point: "The LLM never invents gaps — Python diff with fuzzy '
        'match threshold 85. Every row has evidence and recommended_action."'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="DAT interview 2-minute demo")
    parser.add_argument(
        "--cached",
        action="store_true",
        help="Skip live fetch; print stats from existing workspace artifacts",
    )
    parser.add_argument("--check-links", action="store_true", help="Run link health checks")
    parser.add_argument("--with-popularity", action="store_true", help="Include popularity enrich")
    args = parser.parse_args()

    if not args.cached:
        print("Running full pipeline (use --cached to skip fetch)...")
        code = _run_smoke(check_links=args.check_links, popularity=args.with_popularity)
        if code != 0:
            return code

    _print_headlines()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
