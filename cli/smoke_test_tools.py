#!/usr/bin/env python3
"""Smoke test catalog tools without requiring LLM API keys."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from tools.dot_catalog import fetch_and_save_dot_catalog
from tools.data_gov import fetch_and_save_reference_catalog
from tools.gap_analysis import run_gap_analysis, render_gap_report, export_gaps_csv
from tools.popularity import enrich_popularity_metrics, save_popularity_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="DOT gap analysis smoke test")
    parser.add_argument(
        "--with-popularity",
        action="store_true",
        help="Enrich catalog with view/download counts (slower; ~1 req per dataset)",
    )
    parser.add_argument(
        "--popularity-limit",
        type=int,
        default=200,
        help="Max datasets to enrich for popularity (default 200)",
    )
    parser.add_argument(
        "--check-links",
        action="store_true",
        help="HEAD-check top true_gap landing pages (slower; not default)",
    )
    parser.add_argument(
        "--link-check-limit",
        type=int,
        default=50,
        help="Max URLs to check when --check-links is set (default 50)",
    )
    args = parser.parse_args()

    print("1. Fetching DOT catalog (data.json + views.json)...")
    try:
        dot_datasets, dot_path, sources = fetch_and_save_dot_catalog()
        views_count = sources.get("views_json", {}).get("count", 0)
        enriched = sum(1 for d in dot_datasets if d.get("catalog_source") == "data.json+views.json")
        portal_only = sum(1 for d in dot_datasets if d.get("catalog_source") == "views.json")
        print(f"   OK: {len(dot_datasets)} merged datasets -> {dot_path}")
        print(f"       data.json: {sources.get('data_json', {}).get('count', 0)}")
        print(f"       views.json: {views_count} (enriched {enriched}, portal-only {portal_only})")
    except Exception as exc:
        print(f"   WARN: {exc}")
        dot_path = BACKEND_DIR / "workspace" / "data" / "dot_catalog_snapshot.json"
        if not dot_path.exists():
            raise
        dot_raw = json.loads(dot_path.read_text(encoding="utf-8"))
        dot_datasets = dot_raw.get("datasets", [])
        sources = dot_raw.get("sources", {})
        print(f"   Using cached DOT catalog: {len(dot_datasets)} datasets -> {dot_path}")

    popularity_path = None
    if args.with_popularity:
        print(f"1b. Enriching popularity metrics (limit {args.popularity_limit})...")
        dot_datasets, pop_stats = enrich_popularity_metrics(
            dot_datasets,
            max_enrich=args.popularity_limit,
        )
        dot_path.write_text(
            json.dumps(
                {
                    **json.loads(dot_path.read_text(encoding="utf-8")),
                    "datasets": dot_datasets,
                    "popularity_enriched": pop_stats,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        json_path, md_path, rankings = save_popularity_artifacts(dot_datasets)
        popularity_path = json_path
        print(f"   OK: enriched {pop_stats['enriched']} datasets -> {json_path}")
        print(f"       Report -> {md_path}")
        print(f"       Categories ranked: {len(rankings)}")
        if pop_stats["enriched"] == 0:
            print("   NOTE: Popularity enrich returned 0 — portal API may be unavailable (503). Retry later.")

    print("2. Fetching reference catalog (data.gov + external registries)...")
    try:
        ref_datasets, ref_path, ref_source = fetch_and_save_reference_catalog()
        print(f"   OK: {len(ref_datasets)} datasets via {ref_source} -> {ref_path}")
        by_source: dict[str, int] = {}
        for ds in ref_datasets:
            label = ds.get("source") or "unknown"
            by_source[label] = by_source.get(label, 0) + 1
        top_sources = sorted(by_source.items(), key=lambda x: -x[1])[:8]
        for label, count in top_sources:
            print(f"       {label}: {count}")
    except RuntimeError as exc:
        print(f"   WARN: {exc}")
        ref_path = BACKEND_DIR / "workspace" / "data" / "reference_catalog.json"
        ref_datasets = []
        ref_source = "cached"
        if ref_path.exists():
            ref_raw = json.loads(ref_path.read_text(encoding="utf-8"))
            ref_datasets = ref_raw.get("datasets", [])
            ref_source = ref_raw.get("source", "cached")
            print(f"   Using cached reference catalog: {len(ref_datasets)} datasets ({ref_source})")

    print("3. Running gap analysis...")
    result = run_gap_analysis(
        check_links=args.check_links,
        link_check_limit=args.link_check_limit,
    )
    if popularity_path and popularity_path.exists():
        result["popularity_rankings"] = json.loads(popularity_path.read_text(encoding="utf-8")).get(
            "rankings", {}
        )

    print(f"   OK: {result['gap_count']} gaps")
    print(f"   Summary: {result.get('summary_by_status', {})}")
    gap_class = result.get("gap_class_summary") or {}
    if gap_class:
        print(f"   Gap class: true_gap={gap_class.get('true_gap', 0)}, intentional_external={gap_class.get('intentional_external', 0)}")

    cat_counts = result.get("category_counts") or {}
    print(f"   Canonical categories: {len(cat_counts)} buckets")
    print(f"   Category count total (one per dataset): {result.get('category_count_total', 0)}")

    missing = [g for g in result.get("gaps", []) if g.get("status") == "missing_on_portal"]
    true_gaps = [g for g in missing if g.get("gap_class") == "true_gap"]
    if true_gaps:
        print("   Top 5 true gaps by priority_score:")
        for gap in true_gaps[:5]:
            print(
                f"       [{gap.get('priority_score', 0)}] {gap.get('title')} "
                f"({gap.get('provider', '')}) -> {gap.get('recommended_action', '')}"
            )

    dup_count = result.get("duplicate_cluster_count", 0)
    if dup_count:
        print(f"   Duplicate clusters: {dup_count}")

    if args.check_links:
        link_stats = result.get("link_check_stats") or {}
        print(f"   Link checks: {link_stats}")

    reports_dir = BACKEND_DIR / "workspace" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "gap_analysis_report.md"
    report_path.write_text(render_gap_report(result), encoding="utf-8")
    print(f"4. Report written -> {report_path}")
    csv_path = export_gaps_csv(result)
    print(f"5. Gaps CSV written -> {csv_path}")

    assert len(dot_datasets) > 0, "DOT catalog should not be empty"
    if len(ref_datasets) == 0:
        print("   NOTE: Reference catalog empty. Set DATAGOV_API_KEY in .env for data.gov coverage.")
    else:
        missing = [g for g in result.get("gaps", []) if g.get("status") == "missing_on_portal"]
        true_gaps = [g for g in missing if g.get("gap_class") == "true_gap"]
        print(f"   Missing on portal: {len(missing)} ({len(true_gaps)} true gaps)")
    assert result["gap_count"] > 0, "Expected at least some gaps/issues flagged"
    assert report_path.exists(), "Report file should exist"
    assert len(cat_counts) <= 13, f"Expected canonical categories, got {len(cat_counts)}"

    if args.with_popularity:
        assert popularity_path and popularity_path.exists(), "popularity_rankings.json should exist"
        # Enrichment may be 0 when data.transportation.gov returns 503

    print("\nAll smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
