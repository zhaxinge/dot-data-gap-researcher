"""Enrich DOT catalog datasets with Socrata view/download counts and rankings."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from tools.category_taxonomy import normalize_category

DOT_DOMAIN = "data.transportation.gov"
VIEW_META_URL = f"https://{DOT_DOMAIN}/api/views/{{uid}}.json"
DEFAULT_BATCH_DELAY_MS = 80
DEFAULT_TOP_N = 10


def _workspace_dir() -> Path:
    import os

    default = Path(__file__).resolve().parent.parent / "workspace"
    return Path(os.getenv("DOT_AGENT_WORKSPACE", default)).resolve()


def fetch_view_metrics(socrata_uid: str) -> dict[str, int]:
    """Fetch viewCount and downloadCount for one Socrata asset."""
    uid = (socrata_uid or "").strip().lower()
    if not uid:
        return {}
    try:
        response = requests.get(VIEW_META_URL.format(uid=uid), timeout=30)
        if not response.ok:
            return {}
        payload = response.json()
        return {
            "view_count": int(payload.get("viewCount") or 0),
            "download_count": int(payload.get("downloadCount") or 0),
        }
    except Exception:
        return {}


def enrich_popularity_metrics(
    datasets: list[dict[str, Any]],
    *,
    batch_delay_ms: int = DEFAULT_BATCH_DELAY_MS,
    max_enrich: int | None = None,
    skip_if_present: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Attach view_count and download_count to datasets with socrata_uid."""
    stats = {"enriched": 0, "skipped": 0, "failed": 0}
    limit = max_enrich if max_enrich is not None else len(datasets)

    for i, ds in enumerate(datasets):
        if i >= limit:
            break
        uid = ds.get("socrata_uid") or ds.get("identifier")
        if not uid:
            stats["skipped"] += 1
            continue
        if skip_if_present and ds.get("download_count") is not None:
            stats["skipped"] += 1
            continue

        metrics = fetch_view_metrics(str(uid))
        if metrics:
            ds.update(metrics)
            stats["enriched"] += 1
        else:
            stats["failed"] += 1

        if batch_delay_ms > 0:
            time.sleep(batch_delay_ms / 1000.0)

    return datasets, stats


def _sort_key(ds: dict[str, Any], sort_by: str) -> int:
    if sort_by == "view_count":
        return int(ds.get("view_count") or 0)
    return int(ds.get("download_count") or ds.get("view_count") or 0)


def rank_datasets_by_category(
    datasets: list[dict[str, Any]],
    *,
    sort_by: str = "download_count",
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, list[dict[str, Any]]]:
    """Group datasets by canonical category and return top-N per group."""
    buckets: dict[str, list[dict[str, Any]]] = {}

    for ds in datasets:
        raw = ds.get("domain_category") or ds.get("canonical_category")
        if not raw and ds.get("theme"):
            theme = ds.get("theme")
            raw = theme[0] if isinstance(theme, list) and theme else theme
        cat = normalize_category(str(raw) if raw else None)
        buckets.setdefault(cat, []).append(ds)

    ranked: dict[str, list[dict[str, Any]]] = {}
    for cat, items in buckets.items():
        sorted_items = sorted(items, key=lambda d: _sort_key(d, sort_by), reverse=True)
        ranked[cat] = [
            {
                "title": d.get("title"),
                "socrata_uid": d.get("socrata_uid"),
                "landing_page": d.get("landing_page"),
                "download_count": d.get("download_count", 0),
                "view_count": d.get("view_count", 0),
                "canonical_category": cat,
            }
            for d in sorted_items[:top_n]
        ]
    return ranked


def render_popularity_report(
    rankings: dict[str, list[dict[str, Any]]],
    *,
    sort_by: str = "download_count",
) -> str:
    """Markdown report of popular datasets per category."""
    metric_label = "downloads" if sort_by == "download_count" else "views"
    lines = [
        "# Popular DOT Portal Datasets by Category",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Sorted by:** {metric_label}",
        "",
    ]
    for cat in sorted(rankings.keys()):
        items = rankings[cat]
        if not items or _sort_key({"download_count": items[0].get("download_count"), "view_count": items[0].get("view_count")}, sort_by) == 0:
            continue
        lines.append(f"## {cat}")
        lines.append("")
        for i, item in enumerate(items, 1):
            dl = item.get("download_count", 0)
            vc = item.get("view_count", 0)
            title = item.get("title") or "Untitled"
            url = item.get("landing_page") or ""
            lines.append(f"{i}. **{title}** — {dl:,} downloads, {vc:,} views")
            if url:
                lines.append(f"   - {url}")
        lines.append("")
    return "\n".join(lines)


def save_popularity_artifacts(
    datasets: list[dict[str, Any]],
    *,
    sort_by: str = "download_count",
    top_n: int = DEFAULT_TOP_N,
) -> tuple[Path, Path, dict[str, list[dict[str, Any]]]]:
    """Write popularity_rankings.json and popular_datasets_by_category.md."""
    rankings = rank_datasets_by_category(datasets, sort_by=sort_by, top_n=top_n)
    data_dir = _workspace_dir() / "data"
    reports_dir = _workspace_dir() / "reports"
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    json_path = data_dir / "popularity_rankings.json"
    json_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sort_by": sort_by,
                "top_n": top_n,
                "category_count": len(rankings),
                "rankings": rankings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    md_path = reports_dir / "popular_datasets_by_category.md"
    md_path.write_text(render_popularity_report(rankings, sort_by=sort_by), encoding="utf-8")
    return json_path, md_path, rankings
