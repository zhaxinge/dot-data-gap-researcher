"""Deterministic gap analysis between DOT portal and reference catalogs."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool
from rapidfuzz import fuzz

from tools.category_taxonomy import canonical_category_names, default_category, normalize_category
from tools.data_gov import fetch_and_save_reference_catalog
from tools.dedup import annotate_duplicates, find_duplicate_clusters
from tools.dot_catalog import fetch_and_save_dot_catalog
from tools.gap_scoring import priority_score, sort_gaps_by_priority

MATCH_THRESHOLD = 85
PORTAL_DOMAIN = "data.transportation.gov"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _workspace_dir() -> Path:
    import os

    default = Path(__file__).resolve().parent.parent / "workspace"
    workspace = Path(os.getenv("DOT_AGENT_WORKSPACE", default)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _reference_dir() -> Path:
    return _repo_root() / "data" / "reference"


def _normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _load_yaml(name: str) -> dict[str, Any]:
    path = _reference_dir() / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def _provider_alias_patterns() -> list[tuple[str, list[str]]]:
    config = _load_yaml("provider_aliases.yaml")
    return [
        (entry.get("provider", ""), [p.lower() for p in (entry.get("patterns") or [])])
        for entry in (config.get("aliases") or [])
        if entry.get("provider")
    ]


def normalize_provider(ds: dict[str, Any]) -> str:
    """Map organization/attribution to a short provider label."""
    raw = (
        ds.get("organization")
        or ds.get("attribution")
        or ds.get("publisher")
        or ds.get("bureau")
        or ""
    )
    text = str(raw).lower()
    for provider, patterns in _provider_alias_patterns():
        if any(p in text for p in patterns):
            return provider

    if not raw:
        return "Unknown"
    # Strip parenthetical: "Bureau of Transportation Statistics (BTS)" -> BTS inside parens
    paren = re.search(r"\(([^)]+)\)", str(raw))
    if paren:
        return paren.group(1).strip()
    return str(raw).split(",")[0].strip()[:40]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.replace("+00:00", "Z"), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_reference_gap(reference: dict[str, Any]) -> str:
    """Classify a missing reference entry as true_gap or intentional_external."""
    source = (reference.get("source") or "").lower()

    if source in ("catalog.data.gov", "catalog.data.gov (ckan)"):
        return "true_gap"
    if source.startswith("socrata_discovery"):
        return "true_gap"
    if source.startswith("arcgis_hub:"):
        return "true_gap"
    if source.startswith("registry:"):
        return "intentional_external"
    if source.startswith("external_catalog"):
        return "intentional_external"
    return "true_gap"


def classify_asset_type(ds: dict[str, Any]) -> str:
    """Classify dataset asset type for reporting and priority scoring."""
    source = (ds.get("source") or "").lower()
    if source.startswith("external_catalog") or source.startswith("registry:"):
        return "portal_link"

    title_desc = f"{ds.get('title', '')} {ds.get('description', '')} {ds.get('notes', '')}".lower()
    if any(kw in title_desc for kw in ("report", "publication", "pdf", "briefing")):
        return "report"

    urls = ds.get("distribution_urls") or ds.get("resource_urls") or []
    on_portal = any(PORTAL_DOMAIN in (u or "").lower() for u in urls)
    off_portal = urls and all(PORTAL_DOMAIN not in (u or "").lower() for u in urls)

    if ds.get("has_api_endpoint") and ds.get("socrata_uid"):
        return "api_dataset"
    if on_portal:
        return "downloadable_data"
    if off_portal:
        return "third_party_redirect"
    if ds.get("asset_type") in ("story", "file", "href"):
        return "metadata_only"
    if ds.get("landing_page"):
        return "metadata_only"
    return "metadata_only"


def _keyword_category_scores(ds: dict[str, Any], categories: list[dict[str, Any]]) -> list[tuple[str, int]]:
    text = " ".join(
        [
            ds.get("title") or "",
            ds.get("description") or ds.get("notes") or "",
            " ".join(ds.get("keyword") or ds.get("tags") or ds.get("domain_tags") or []),
        ]
    ).lower()

    scores: list[tuple[str, int]] = []
    for cat in categories:
        keywords = cat.get("keywords") or []
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits:
            scores.append((normalize_category(cat.get("name") or cat.get("id", "")), hits))
    return sorted(scores, key=lambda x: (-x[1], x[0]))


def primary_category(ds: dict[str, Any], categories: list[dict[str, Any]]) -> str:
    """Single canonical category for counting and CSV export."""
    raw = ds.get("domain_category") or ds.get("canonical_category")
    if raw:
        return normalize_category(str(raw))

    scores = _keyword_category_scores(ds, categories)
    if scores:
        return scores[0][0]
    return default_category()


def secondary_categories(ds: dict[str, Any], categories: list[dict[str, Any]]) -> list[str]:
    """Optional secondary tags when multiple categories score within 1 of top."""
    raw = ds.get("domain_category")
    if raw:
        return []

    scores = _keyword_category_scores(ds, categories)
    if not scores:
        return []
    top_score = scores[0][1]
    return [name for name, score in scores if score >= top_score - 1 and score > 0]


def categorize_dataset(ds: dict[str, Any], categories: list[dict[str, Any]]) -> list[str]:
    """Primary plus secondary categories."""
    primary = primary_category(ds, categories)
    secondary = [c for c in secondary_categories(ds, categories) if c != primary]
    return [primary, *secondary]


def build_dot_index(dot_datasets: list[dict[str, Any]]) -> dict[str, Any]:
    """Build lookup structures for matching."""
    by_identifier: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    for ds in dot_datasets:
        ident = (ds.get("identifier") or "").strip().lower()
        if ident:
            by_identifier[ident] = ds
        title_key = _normalize_title(ds.get("title") or "")
        if title_key:
            by_title[title_key] = ds
        for url_field in ("landing_page",):
            url = (ds.get(url_field) or "").strip().lower().rstrip("/")
            if url:
                by_url[url] = ds
        for url in ds.get("distribution_urls") or []:
            norm = (url or "").strip().lower().rstrip("/")
            if norm:
                by_url[norm] = ds

    return {"by_identifier": by_identifier, "by_url": by_url, "by_title": by_title, "all": dot_datasets}


def matched_in_dot(
    reference: dict[str, Any], dot_index: dict[str, Any]
) -> tuple[bool, str | None, int]:
    """Return whether reference appears on DOT portal, match reason, and best fuzzy score."""
    ref_ident = (reference.get("identifier") or "").strip().lower()
    if ref_ident and ref_ident in dot_index["by_identifier"]:
        return True, "identifier", 100

    for url in [reference.get("landing_page"), *(reference.get("resource_urls") or [])]:
        norm = (url or "").strip().lower().rstrip("/")
        if norm and norm in dot_index["by_url"]:
            return True, "url", 100

    ref_title = _normalize_title(reference.get("title") or "")
    if ref_title in dot_index["by_title"]:
        return True, "exact_title", 100

    best_score = 0
    for dot_title in dot_index["by_title"]:
        score = fuzz.token_sort_ratio(ref_title, dot_title)
        if score > best_score:
            best_score = score
    if best_score >= MATCH_THRESHOLD:
        return True, f"fuzzy_title:{best_score}", best_score

    return False, None, best_score


def build_match_evidence(
    reference: dict[str, Any],
    *,
    matched: bool,
    match_reason: str | None,
    best_fuzzy_score: int,
) -> dict[str, Any]:
    """Audit trail for why a reference was flagged as missing."""
    return {
        "match_attempted": ["identifier", "url", "exact_title", "fuzzy_title"],
        "matched": matched,
        "match_reason": match_reason,
        "best_fuzzy_score": best_fuzzy_score,
        "match_threshold": MATCH_THRESHOLD,
        "reference_source": reference.get("source", "catalog.data.gov"),
    }


def recommended_action(gap: dict[str, Any]) -> str:
    """Deterministic next-step recommendation (not LLM-generated)."""
    status = gap.get("status", "")
    gap_class = gap.get("gap_class", "")
    asset_type = gap.get("asset_type", "")

    if status == "missing_on_portal":
        if gap_class == "intentional_external" or asset_type in ("portal_link", "third_party_redirect"):
            return "link_only"
        return "mirror_on_portal"
    if status == "stale_metadata":
        return "refresh_metadata"
    if status == "redirect_only":
        return "link_only"
    if status == "no_api_endpoint":
        return "add_api_endpoint"
    if status == "category_empty":
        return "populate_category"
    return "review"


def provider_issue_rollup(gaps: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count quality issues by normalized provider."""
    rollup: dict[str, dict[str, int]] = {}
    quality_statuses = {"stale_metadata", "no_api_endpoint", "redirect_only", "missing_on_portal"}

    for gap in gaps:
        if gap.get("status") not in quality_statuses:
            continue
        provider = gap.get("provider") or "Unknown"
        rollup.setdefault(provider, {})
        status = gap.get("status", "unknown")
        rollup[provider][status] = rollup[provider].get(status, 0) + 1

    return rollup


def find_gaps(
    dot_datasets: list[dict[str, Any]],
    reference_datasets: list[dict[str, Any]],
    stale_threshold_days: int = 730,
    *,
    check_links: bool = False,
    link_check_limit: int = 50,
) -> dict[str, Any]:
    """Compute gap list and quality issues."""
    dot_index = build_dot_index(dot_datasets)
    expected = _load_yaml("expected_topics.yaml")
    categories = expected.get("categories") or []
    gaps: list[dict[str, Any]] = []

    for ref in reference_datasets:
        is_matched, reason, best_fuzzy = matched_in_dot(ref, dot_index)
        if not is_matched:
            gap_class = classify_reference_gap(ref)
            asset_type = classify_asset_type(ref)
            primary = primary_category(ref, categories)
            gap = {
                "status": "missing_on_portal",
                "gap_class": gap_class,
                "title": ref.get("title"),
                "source": ref.get("source", "catalog.data.gov"),
                "landing_page": ref.get("landing_page"),
                "organization": ref.get("organization"),
                "provider": normalize_provider(ref),
                "modified": ref.get("modified"),
                "access": ref.get("access"),
                "raw_category": ref.get("domain_category"),
                "primary_category": primary,
                "categories": categorize_dataset(ref, categories),
                "asset_type": asset_type,
                "evidence": build_match_evidence(
                    ref, matched=False, match_reason=reason, best_fuzzy_score=best_fuzzy
                ),
            }
            gap["recommended_action"] = recommended_action(gap)
            gap["priority_score"] = priority_score(gap)
            gaps.append(gap)

    category_counts: dict[str, int] = {name: 0 for name in canonical_category_names()}
    if default_category() not in category_counts:
        category_counts[default_category()] = 0

    for ds in dot_datasets:
        raw_cat = ds.get("domain_category")
        if raw_cat:
            ds["raw_category"] = raw_cat
            ds["canonical_category"] = normalize_category(str(raw_cat))
        primary = primary_category(ds, categories)
        ds["primary_category"] = primary
        category_counts[primary] = category_counts.get(primary, 0) + 1

    now = datetime.now(timezone.utc)
    for cat_name, count in category_counts.items():
        if count == 0 and cat_name:
            gap = {"status": "category_empty", "category": cat_name, "primary_category": cat_name}
            gap["provider"] = "USDOT"
            gap["recommended_action"] = recommended_action(gap)
            gaps.append(gap)

    for ds in dot_datasets:
        provider = normalize_provider(ds)
        asset_type = classify_asset_type(ds)

        if not ds.get("has_api_endpoint"):
            gap = {
                "status": "no_api_endpoint",
                "title": ds.get("title"),
                "landing_page": ds.get("landing_page"),
                "socrata_uid": ds.get("socrata_uid"),
                "provider": provider,
                "primary_category": ds.get("primary_category"),
                "asset_type": asset_type,
            }
            gap["recommended_action"] = recommended_action(gap)
            gaps.append(gap)

        modified = _parse_date(ds.get("modified"))
        if modified:
            age_days = (now - modified).days
            if age_days > stale_threshold_days:
                gap = {
                    "status": "stale_metadata",
                    "title": ds.get("title"),
                    "modified": ds.get("modified"),
                    "age_days": age_days,
                    "landing_page": ds.get("landing_page"),
                    "provider": provider,
                    "primary_category": ds.get("primary_category"),
                    "asset_type": asset_type,
                }
                gap["recommended_action"] = recommended_action(gap)
                gaps.append(gap)

        if ds.get("distribution_urls") and not ds.get("has_api_endpoint"):
            only_external = all(
                PORTAL_DOMAIN not in (url or "").lower()
                for url in ds.get("distribution_urls") or []
            )
            if only_external:
                gap = {
                    "status": "redirect_only",
                    "title": ds.get("title"),
                    "landing_page": ds.get("landing_page"),
                    "distribution_urls": ds.get("distribution_urls"),
                    "provider": provider,
                    "primary_category": ds.get("primary_category"),
                    "asset_type": "third_party_redirect",
                }
                gap["recommended_action"] = recommended_action(gap)
                gaps.append(gap)

    gaps = annotate_duplicates(gaps, dot_datasets)
    for gap in gaps:
        if "priority_score" not in gap:
            gap["priority_score"] = priority_score(gap)

    gaps = sort_gaps_by_priority(gaps)

    if check_links:
        from tools.link_health import enrich_gaps_link_health

        gaps, link_stats = enrich_gaps_link_health(gaps, max_checks=link_check_limit)
    else:
        link_stats = {}

    duplicate_clusters = find_duplicate_clusters(dot_datasets)
    missing_gaps = [g for g in gaps if g.get("status") == "missing_on_portal"]
    gap_class_summary = {
        "true_gap": sum(1 for g in missing_gaps if g.get("gap_class") == "true_gap"),
        "intentional_external": sum(1 for g in missing_gaps if g.get("gap_class") == "intentional_external"),
    }

    return {
        "generated_at": now.isoformat(),
        "dot_dataset_count": len(dot_datasets),
        "reference_dataset_count": len(reference_datasets),
        "gap_count": len(gaps),
        "category_counts": category_counts,
        "category_count_total": sum(category_counts.values()),
        "gap_class_summary": gap_class_summary,
        "provider_issue_rollup": provider_issue_rollup(gaps),
        "duplicate_clusters": duplicate_clusters[:20],
        "duplicate_cluster_count": len(duplicate_clusters),
        "link_check_stats": link_stats,
        "gaps": gaps,
        "summary_by_status": _summarize_by_status(gaps),
    }


def _summarize_by_status(gaps: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for gap in gaps:
        status = gap.get("status", "unknown")
        summary[status] = summary.get(status, 0) + 1
    return summary


def _load_popularity_rankings() -> dict[str, list[dict[str, Any]]]:
    path = _workspace_dir() / "data" / "popularity_rankings.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("rankings") or {}
    except Exception:
        return {}


def _load_or_fetch_catalogs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data_dir = _workspace_dir() / "data"
    dot_path = data_dir / "dot_catalog_snapshot.json"
    ref_path = data_dir / "reference_catalog.json"

    if dot_path.exists():
        dot_raw = json.loads(dot_path.read_text(encoding="utf-8"))
        dot_datasets = dot_raw.get("datasets", [])
    else:
        dot_datasets, _, _ = fetch_and_save_dot_catalog()

    if ref_path.exists():
        ref_raw = json.loads(ref_path.read_text(encoding="utf-8"))
        reference_datasets = ref_raw.get("datasets", [])
    else:
        reference_datasets, _, _ = fetch_and_save_reference_catalog()

    return dot_datasets, reference_datasets


def _flatten_gap_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v is not None)
    return str(value)


def export_gaps_csv(
    gap_result: dict[str, Any] | None = None,
    *,
    output_path: Path | None = None,
) -> Path:
    """Write gaps from gap_result (or gaps.json) to a flat CSV file."""
    if gap_result is None:
        gaps_path = _workspace_dir() / "data" / "gaps.json"
        gap_result = json.loads(gaps_path.read_text(encoding="utf-8"))

    data_dir = _workspace_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_path or data_dir / "gaps.csv"

    fieldnames = [
        "priority_score",
        "status",
        "gap_class",
        "title",
        "provider",
        "organization",
        "source",
        "primary_category",
        "asset_type",
        "recommended_action",
        "landing_page",
        "link_status",
        "modified",
        "access",
        "raw_category",
        "categories",
        "category",
        "age_days",
        "socrata_uid",
        "duplicate_of",
        "distribution_urls",
        "evidence",
    ]

    gaps = sort_gaps_by_priority(gap_result.get("gaps") or [])

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for gap in gaps:
            writer.writerow(
                {
                    "priority_score": gap.get("priority_score", ""),
                    "status": gap.get("status", ""),
                    "gap_class": gap.get("gap_class", ""),
                    "title": gap.get("title", ""),
                    "provider": gap.get("provider", ""),
                    "organization": gap.get("organization", ""),
                    "source": gap.get("source", ""),
                    "primary_category": gap.get("primary_category", ""),
                    "asset_type": gap.get("asset_type", ""),
                    "recommended_action": gap.get("recommended_action", ""),
                    "landing_page": gap.get("landing_page", ""),
                    "link_status": gap.get("link_status", ""),
                    "modified": gap.get("modified", ""),
                    "access": gap.get("access", ""),
                    "raw_category": gap.get("raw_category", ""),
                    "categories": _flatten_gap_value(gap.get("categories")),
                    "category": gap.get("category", ""),
                    "age_days": gap.get("age_days", ""),
                    "socrata_uid": gap.get("socrata_uid", ""),
                    "duplicate_of": gap.get("duplicate_of", ""),
                    "distribution_urls": _flatten_gap_value(gap.get("distribution_urls")),
                    "evidence": _flatten_gap_value(gap.get("evidence")),
                }
            )

    return csv_path


def run_gap_analysis(*, check_links: bool = False, link_check_limit: int = 50) -> dict[str, Any]:
    """Run full gap analysis and persist gaps.json + gaps.csv."""
    expected = _load_yaml("expected_topics.yaml")
    stale_days = expected.get("stale_threshold_days", 730)

    dot_datasets, reference_datasets = _load_or_fetch_catalogs()
    result = find_gaps(
        dot_datasets,
        reference_datasets,
        stale_threshold_days=stale_days,
        check_links=check_links,
        link_check_limit=link_check_limit,
    )

    data_dir = _workspace_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gaps_path = data_dir / "gaps.json"
    gaps_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    csv_path = export_gaps_csv(result)
    result["gaps_path"] = str(gaps_path)
    result["gaps_csv_path"] = str(csv_path)
    return result


def render_gap_report(gap_result: dict[str, Any]) -> str:
    """Render human-readable markdown report."""
    lines = [
        "# DOT Open Data Gap Analysis Report",
        "",
        f"**Generated:** {gap_result.get('generated_at', 'unknown')}",
        "",
        "## Executive Summary",
        "",
        f"- DOT portal datasets: **{gap_result.get('dot_dataset_count', 0)}**",
        f"- Reference datasets: **{gap_result.get('reference_dataset_count', 0)}**",
        f"- Total gaps/issues flagged: **{gap_result.get('gap_count', 0)}**",
        f"- Category assignments (one per dataset): **{gap_result.get('category_count_total', 0)}**",
        "",
    ]

    gap_class = gap_result.get("gap_class_summary") or {}
    if gap_class:
        lines.extend(
            [
                "### Missing-on-Portal Breakdown",
                "",
                f"- **True gaps** (federal/cross-portal datasets to mirror): **{gap_class.get('true_gap', 0)}**",
                f"- **Intentionally external** (registries, partner portals): **{gap_class.get('intentional_external', 0)}**",
                "",
            ]
        )

    summary = gap_result.get("summary_by_status") or {}
    if summary:
        lines.append("### Gaps by Status")
        lines.append("")
        for status, count in sorted(summary.items()):
            lines.append(f"- **{status}**: {count}")
        lines.append("")

    rollup = gap_result.get("provider_issue_rollup") or {}
    if rollup:
        lines.extend(["## Issues by Provider", ""])
        for provider in sorted(rollup.keys()):
            counts = rollup[provider]
            parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
            lines.append(f"- **{provider}** — {', '.join(parts)}")
        lines.append("")

    dup_count = gap_result.get("duplicate_cluster_count", 0)
    if dup_count:
        lines.extend(
            [
                f"## Duplicate Clusters ({dup_count} groups)",
                "",
            ]
        )
        for cluster in (gap_result.get("duplicate_clusters") or [])[:10]:
            members = ", ".join(m.get("title", "") for m in cluster.get("members", [])[:3])
            lines.append(f"- **{cluster.get('canonical_title')}** — {members}")
        lines.append("")

    lines.extend(["## Coverage by Portal Category", ""])
    for cat, count in sorted((gap_result.get("category_counts") or {}).items()):
        lines.append(f"- **{cat}**: {count} datasets")
    lines.append("")

    rankings = gap_result.get("popularity_rankings") or _load_popularity_rankings()
    if rankings:
        lines.extend(["## Most Popular Datasets by Category", ""])
        lines.append("_Top entries by download count (fallback: view count)._")
        lines.append("")
        for cat in sorted(rankings.keys()):
            items = rankings[cat]
            if not items:
                continue
            top = items[0]
            if not (top.get("download_count") or top.get("view_count")):
                continue
            lines.append(f"### {cat}")
            lines.append("")
            for i, item in enumerate(items[:10], 1):
                dl = item.get("download_count", 0)
                vc = item.get("view_count", 0)
                title = item.get("title") or "Untitled"
                lines.append(f"{i}. **{title}** — {dl:,} downloads, {vc:,} views")
                if item.get("landing_page"):
                    lines.append(f"   - {item['landing_page']}")
            lines.append("")

    gaps = gap_result.get("gaps") or []
    missing = [g for g in gaps if g.get("status") == "missing_on_portal"]
    true_gaps = sort_gaps_by_priority([g for g in missing if g.get("gap_class") == "true_gap"])
    external = [g for g in missing if g.get("gap_class") == "intentional_external"]

    if true_gaps:
        lines.extend(
            [
                "## True Gaps — Missing on data.transportation.gov",
                "",
                "_Sorted by priority_score (federal downloadable/API sources first)._",
                "",
            ]
        )
        for gap in true_gaps[:25]:
            score = gap.get("priority_score", 0)
            action = gap.get("recommended_action", "")
            lines.append(
                f"- **[{score}] {gap.get('title')}** ({gap.get('provider', 'unknown')}) — _{action}_"
            )
            if gap.get("landing_page"):
                lines.append(f"  - Source: {gap['landing_page']}")
            if gap.get("link_status"):
                lines.append(f"  - Link: {gap['link_status']}")
        if len(true_gaps) > 25:
            lines.append(f"- ... and {len(true_gaps) - 25} more true gaps (see gaps.csv)")
        lines.append("")

    if external:
        lines.extend(
            [
                "## Intentionally External Sources",
                "",
                "_Partner portals and registries — recommend link_only, not full portal mirror._",
                "",
            ]
        )
        for gap in external[:20]:
            lines.append(f"- **{gap.get('title')}** ({gap.get('provider', 'unknown')})")
            if gap.get("landing_page"):
                lines.append(f"  - Source: {gap['landing_page']}")
        if len(external) > 20:
            lines.append(f"- ... and {len(external) - 20} more (see gaps.csv)")
        lines.append("")

    quality_statuses = {"stale_metadata", "no_api_endpoint", "redirect_only", "category_empty"}
    quality = [g for g in gaps if g.get("status") in quality_statuses]
    if quality:
        lines.extend(["## Data Quality Issues", ""])
        for gap in quality[:20]:
            lines.append(
                f"- **{gap.get('status')}**: {gap.get('title') or gap.get('category')} "
                f"({gap.get('provider', '')}) — {gap.get('recommended_action', '')}"
            )
        if len(quality) > 20:
            lines.append(f"- ... and {len(quality) - 20} more (see gaps.csv)")
        lines.append("")

    lines.extend(
        [
            "## Recommended Additions",
            "",
            "1. Mirror high-priority **true_gap** rows from gaps.csv (sort by priority_score).",
            "2. Use **link_only** for intentionally external and third_party_redirect assets.",
            "3. Refresh **stale_metadata** datasets older than 2 years.",
            "4. Add API endpoints for **no_api_endpoint** portal rows.",
            "5. Every gap includes **evidence** and **recommended_action** for audit trail.",
            "",
            "## Sources",
            "",
            "- https://data.transportation.gov/data.json",
            "- https://catalog.data.gov/organization/dot",
            "- data/reference/modal_agencies.yaml",
            "- data/reference/external_catalogs.yaml",
            "- data/reference/provider_verification.yaml",
            "",
        ]
    )
    return "\n".join(lines)


@tool(parse_docstring=True)
def analyze_catalog_gaps() -> str:
    """Run deterministic gap analysis between DOT portal and data.gov catalogs.

    Compares workspace snapshots (or fetches fresh catalogs), matches datasets
    by identifier, URL, and fuzzy title, and flags missing, stale, and
    quality issues. Saves workspace/data/gaps.json and gaps.csv.

    Returns:
        JSON string with gap summary and file path.
    """
    try:
        result = run_gap_analysis()
        return json.dumps(
            {
                "status": "ok",
                "gaps_path": result.get("gaps_path"),
                "gaps_csv_path": result.get("gaps_csv_path"),
                "gap_count": result.get("gap_count"),
                "summary_by_status": result.get("summary_by_status"),
                "gap_class_summary": result.get("gap_class_summary"),
                "category_counts": result.get("category_counts"),
                "category_count_total": result.get("category_count_total"),
                "sample_gaps": (result.get("gaps") or [])[:10],
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@tool(parse_docstring=True)
def export_gap_report() -> str:
    """Export a markdown gap analysis report to workspace/reports/.

    Runs gap analysis if gaps.json is missing, then writes
    workspace/reports/gap_analysis_report.md and gaps.csv.

    Returns:
        JSON string with report path and summary.
    """
    try:
        gaps_path = _workspace_dir() / "data" / "gaps.json"
        if gaps_path.exists():
            result = json.loads(gaps_path.read_text(encoding="utf-8"))
        else:
            result = run_gap_analysis()

        reports_dir = _workspace_dir() / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "gap_analysis_report.md"
        report_path.write_text(render_gap_report(result), encoding="utf-8")
        csv_path = export_gaps_csv(result)

        return json.dumps(
            {
                "status": "ok",
                "report_path": str(report_path),
                "gaps_path": str(gaps_path if gaps_path.exists() else _workspace_dir() / "data" / "gaps.json"),
                "gaps_csv_path": str(csv_path),
                "gap_count": result.get("gap_count"),
                "summary_by_status": result.get("summary_by_status"),
                "gap_class_summary": result.get("gap_class_summary"),
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})
