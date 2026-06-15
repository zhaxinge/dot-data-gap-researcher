"""Fetch and parse the DOT catalog from data.json and Socrata Discovery API."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from langchain_core.tools import tool

DOT_CATALOG_URL = "https://data.transportation.gov/data.json"
VIEWS_CATALOG_URL = "https://data.transportation.gov/api/views.json"
SOCRATA_DISCOVERY_URL = "https://api.us.socrata.com/api/catalog/v1"
DOT_DOMAIN = "data.transportation.gov"
SOCRATA_UID_RE = re.compile(r"/([a-z0-9]{4}-[a-z0-9]{4})(?:/|$)", re.I)
DISCOVERY_PAGE_SIZE = 1000
ENRICHABLE_ASSET_TYPES = {"dataset", "map", "file", "href", "federated_href"}


def _workspace_dir() -> Path:
    import os

    default = Path(__file__).resolve().parent.parent / "workspace"
    workspace = Path(os.getenv("DOT_AGENT_WORKSPACE", default)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _extract_socrata_uid(landing_page: str | None, distributions: list[dict]) -> str | None:
    candidates: list[str] = []
    if landing_page:
        match = SOCRATA_UID_RE.search(landing_page)
        if match:
            candidates.append(match.group(1).lower())
    for dist in distributions:
        for key in ("downloadURL", "accessURL", "conformsTo"):
            url = dist.get(key) or ""
            match = SOCRATA_UID_RE.search(url)
            if match:
                candidates.append(match.group(1).lower())
    return candidates[0] if candidates else None


def _has_api_endpoint(distributions: list[dict], socrata_uid: str | None) -> bool:
    if socrata_uid:
        return True
    for dist in distributions:
        for key in ("downloadURL", "accessURL", "mediaType"):
            value = (dist.get(key) or "").lower()
            if "api" in value or "json" in value or "csv" in value:
                return True
    return False


def parse_dot_catalog(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Project Open Data catalog entries."""
    datasets = raw.get("dataset", [])
    parsed: list[dict[str, Any]] = []

    for item in datasets:
        distributions = item.get("distribution") or []
        landing_page = item.get("landingPage")
        socrata_uid = _extract_socrata_uid(landing_page, distributions)

        parsed.append(
            {
                "title": item.get("title", "").strip(),
                "identifier": item.get("identifier", "").strip(),
                "description": (item.get("description") or "")[:500],
                "bureau": item.get("bureauCode", []),
                "theme": item.get("theme", []),
                "keyword": item.get("keyword", []),
                "modified": item.get("modified") or item.get("issued"),
                "landing_page": landing_page,
                "distribution_urls": [
                    d.get("downloadURL") or d.get("accessURL")
                    for d in distributions
                    if d.get("downloadURL") or d.get("accessURL")
                ],
                "socrata_uid": socrata_uid,
                "has_api_endpoint": _has_api_endpoint(distributions, socrata_uid),
                "publisher": (item.get("publisher") or {}).get("name"),
                "catalog_source": "data.json",
            }
        )

    return parsed


def _epoch_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value) if value else None


def parse_views_catalog_entry(view: dict[str, Any]) -> dict[str, Any]:
    """Normalize an entry from data.transportation.gov/api/views.json."""
    uid = (view.get("id") or "").lower()
    asset_type = view.get("assetType") or "dataset"
    category = view.get("category")

    return {
        "title": (view.get("name") or "").strip(),
        "identifier": uid,
        "socrata_uid": uid,
        "description": (view.get("description") or "")[:500],
        "modified": _epoch_to_iso(view.get("viewLastModified")),
        "data_updated_at": _epoch_to_iso(view.get("rowsUpdatedAt")),
        "landing_page": f"https://data.transportation.gov/d/{uid}",
        "domain_category": category,
        "asset_type": asset_type,
        "tags": view.get("tags") or [],
        "attribution": view.get("attribution"),
        "publisher": view.get("attribution"),
        "has_api_endpoint": asset_type in {"dataset", "map", "file"},
        "hide_from_data_json": view.get("hideFromDataJson", False),
        "catalog_source": "views.json",
    }


def fetch_socrata_views_catalog() -> list[dict[str, Any]]:
    """Fetch full portal catalog from native Socrata views.json."""
    response = requests.get(VIEWS_CATALOG_URL, timeout=180)
    response.raise_for_status()
    views = response.json()
    if not isinstance(views, list):
        return []

    parsed: list[dict[str, Any]] = []
    for view in views:
        if view.get("hideFromCatalog"):
            continue
        asset_type = view.get("assetType") or ""
        if asset_type not in ENRICHABLE_ASSET_TYPES:
            continue
        parsed.append(parse_views_catalog_entry(view))
    return parsed


def parse_socrata_discovery_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Socrata Discovery API result."""
    resource = entry.get("resource") or {}
    classification = entry.get("classification") or {}
    metadata = entry.get("metadata") or {}
    uid = (resource.get("id") or "").lower()

    domain_metadata = {
        item.get("key"): item.get("value")
        for item in (classification.get("domain_metadata") or [])
        if item.get("key")
    }

    return {
        "title": (resource.get("name") or "").strip(),
        "identifier": uid,
        "socrata_uid": uid,
        "description": (resource.get("description") or "")[:500],
        "modified": resource.get("metadata_updated_at") or resource.get("updatedAt"),
        "data_updated_at": resource.get("data_updated_at"),
        "landing_page": entry.get("permalink") or entry.get("link"),
        "permalink": entry.get("permalink"),
        "link": entry.get("link"),
        "domain": metadata.get("domain"),
        "domain_category": classification.get("domain_category"),
        "domain_tags": classification.get("domain_tags") or [],
        "categories": classification.get("categories") or [],
        "tags": classification.get("tags") or [],
        "column_names": resource.get("columns_name") or [],
        "column_field_names": resource.get("columns_field_name") or [],
        "attribution": resource.get("attribution"),
        "publisher": resource.get("attribution"),
        "has_api_endpoint": True,
        "catalog_source": "socrata_discovery",
        "domain_metadata": domain_metadata,
    }


def fetch_socrata_discovery_catalog(
    domain: str = DOT_DOMAIN,
    only: str = "dataset",
    limit: int = DISCOVERY_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch all datasets for a Socrata domain using scroll pagination."""
    all_entries: list[dict[str, Any]] = []
    scroll_id: str | None = "0"

    while True:
        params: dict[str, Any] = {
            "domains": domain,
            "only": only,
            "limit": limit,
        }
        if scroll_id is not None:
            params["scroll_id"] = scroll_id

        response = requests.get(SOCRATA_DISCOVERY_URL, params=params, timeout=120)
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") or []
        if not results:
            break

        all_entries.extend(parse_socrata_discovery_entry(item) for item in results)

        if len(results) < limit:
            break

        last_id = (results[-1].get("resource") or {}).get("id")
        if not last_id:
            break
        scroll_id = last_id

    return all_entries


def merge_catalog_entries(
    data_json_datasets: list[dict[str, Any]],
    views_datasets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge data.json entries with views.json metadata by socrata_uid."""
    views_by_uid = {
        (d.get("socrata_uid") or "").lower(): d
        for d in views_datasets
        if d.get("socrata_uid")
    }

    merged: list[dict[str, Any]] = []
    seen_uids: set[str] = set()

    for entry in data_json_datasets:
        uid = (entry.get("socrata_uid") or "").lower()
        combined = dict(entry)
        if uid and uid in views_by_uid:
            view = views_by_uid[uid]
            combined.update(
                {
                    "domain_category": view.get("domain_category"),
                    "asset_type": view.get("asset_type"),
                    "data_updated_at": view.get("data_updated_at"),
                    "tags": view.get("tags") or combined.get("keyword"),
                    "hide_from_data_json": view.get("hide_from_data_json"),
                    "catalog_source": "data.json+views.json",
                }
            )
            if view.get("modified"):
                combined["modified"] = view["modified"]
            if view.get("has_api_endpoint"):
                combined["has_api_endpoint"] = True
            seen_uids.add(uid)
        merged.append(combined)

    for view in views_datasets:
        uid = (view.get("socrata_uid") or "").lower()
        if uid and uid not in seen_uids:
            merged.append(view)

    return merged


def fetch_data_json_catalog() -> list[dict[str, Any]]:
    """Download and parse data.transportation.gov/data.json."""
    response = requests.get(DOT_CATALOG_URL, timeout=120)
    response.raise_for_status()
    return parse_dot_catalog(response.json())


def fetch_and_save_dot_catalog() -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    """Download DOT catalog from data.json + views.json, merge, and save."""
    data_json_datasets = fetch_data_json_catalog()
    views_datasets: list[dict[str, Any]] = []
    views_error: str | None = None

    try:
        views_datasets = fetch_socrata_views_catalog()
    except Exception as exc:
        views_error = str(exc)

    merged = merge_catalog_entries(data_json_datasets, views_datasets)

    data_dir = _workspace_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()

    views_path = data_dir / "socrata_views_snapshot.json"
    views_path.write_text(
        json.dumps(
            {
                "fetched_at": fetched_at,
                "source": VIEWS_CATALOG_URL,
                "dataset_count": len(views_datasets),
                "error": views_error,
                "datasets": views_datasets,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    snapshot_path = data_dir / "dot_catalog_snapshot.json"
    meta = {
        "fetched_at": fetched_at,
        "sources": {
            "data_json": {"url": DOT_CATALOG_URL, "count": len(data_json_datasets)},
            "views_json": {
                "url": VIEWS_CATALOG_URL,
                "count": len(views_datasets),
                "error": views_error,
            },
        },
        "merged_count": len(merged),
        "enriched_count": sum(1 for d in merged if d.get("catalog_source") == "data.json+views.json"),
        "portal_only_count": sum(1 for d in merged if d.get("catalog_source") == "views.json"),
        "datasets": merged,
    }
    snapshot_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return merged, snapshot_path, meta["sources"]


@tool(parse_docstring=True)
def fetch_dot_catalog() -> str:
    """Fetch the full data.transportation.gov catalog.

    Downloads data.json (Project Open Data) and enriches it with metadata from
    the portal's native Socrata catalog (api/views.json): categories, asset
    types, and update dates. Saves merged results to dot_catalog_snapshot.json
    and raw views to socrata_views_snapshot.json.

    Returns:
        JSON string with catalog summary and snapshot paths.
    """
    try:
        merged, snapshot_path, sources = fetch_and_save_dot_catalog()
        views_path = _workspace_dir() / "data" / "socrata_views_snapshot.json"
        summary = {
            "status": "ok",
            "dataset_count": len(merged),
            "snapshot_path": str(snapshot_path),
            "views_snapshot_path": str(views_path),
            "sources": sources,
            "enriched_count": sum(1 for d in merged if d.get("catalog_source") == "data.json+views.json"),
            "portal_only_count": sum(1 for d in merged if d.get("catalog_source") == "views.json"),
            "with_api_endpoint": sum(1 for d in merged if d.get("has_api_endpoint")),
            "without_api_endpoint": sum(1 for d in merged if not d.get("has_api_endpoint")),
            "sample": merged[:5],
        }
        return json.dumps(summary, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})
