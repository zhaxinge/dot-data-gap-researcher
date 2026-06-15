#!/usr/bin/env python3
"""One-shot automated DOT data gap analysis."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Add backend to path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(REPO_ROOT / ".env")

from agent import create_agent  # noqa: E402

GAP_ANALYSIS_PROMPT = """Run a full DOT open data gap analysis for https://data.transportation.gov.

Follow the transportation research methodology:
1. Call fetch_dot_catalog to snapshot the DOT portal catalog.
2. Call search_data_gov with query "organization:dot".
3. Call analyze_catalog_gaps to produce gaps.json with deterministic matching.
4. Optionally use web search to note datasets on FAA, FHWA, FRA, FTA, or NHTSA portals.
5. Call export_gap_report to write the markdown report.

Then summarize:
- Executive Summary
- Top missing datasets on data.transportation.gov
- Empty or weak portal categories
- Data quality issues (stale, no API, redirect-only)
- Prioritized recommendations

Cite evidence from tool outputs only."""


async def run() -> int:
    print("Creating DOT Data Gap Research Agent...")
    agent, sandbox = create_agent(
        skill_ids=["websearch", "fileio", "execute", "transportation_research"],
        model_id="llama",
        hitl_enabled=False,
    )

    print("Running gap analysis...")
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": GAP_ANALYSIS_PROMPT}]},
            config={"configurable": {"thread_id": "cli-gap-analysis"}, "recursion_limit": 120},
        )
        last_msg = result["messages"][-1]
        content = getattr(last_msg, "content", str(last_msg))
        print("\n" + "=" * 60)
        print("AGENT SUMMARY")
        print("=" * 60)
        print(content[:4000])
        if len(str(content)) > 4000:
            print("\n... (truncated)")

        workspace = BACKEND_DIR / "workspace"
        gaps_path = workspace / "data" / "gaps.json"
        report_path = workspace / "reports" / "gap_analysis_report.md"

        print("\n" + "=" * 60)
        print("ARTIFACTS")
        print("=" * 60)
        for path in [gaps_path, report_path, workspace / "data" / "dot_catalog_snapshot.json"]:
            status = "OK" if path.exists() else "MISSING"
            print(f"  [{status}] {path}")

        if gaps_path.exists():
            gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
            print(f"\nGap count: {gaps.get('gap_count', 0)}")
            print(f"Summary: {gaps.get('summary_by_status', {})}")

        return 0
    finally:
        if sandbox is not None:
            try:
                sandbox.delete()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
