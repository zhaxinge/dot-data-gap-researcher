"""Search catalog.data.gov and Socrata Discovery for DOT-related datasets."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from langchain_core.tools import tool

from tools.dot_catalog import (
    DOT_DOMAIN,
    SOCRATA_DISCOVERY_URL,
    parse_socrata_discovery_entry,
)
from tools.external_catalogs import fetch_external_reference_catalog, load_external_catalogs

CKAN_SEARCH_URL = "https://catalog.data.gov/api/3/action/package_search"
DATAGOV_API_BASE = "https://api.gsa.gov/technology/datagov/v4"
DEFAULT_ORG_SLUG = "dot"
ORG_SLUG_ALIASES = {
    "dot-gov": "dot",
    "department-of-transportation": "dot",
    "usdot": "dot",
}
def _cross_portal_queries() -> list[str]:
    config = load_external_catalogs()
    return config.get("socrata_discovery_queries") or [
        "Department of Transportation",
        "Federal Aviation Administration",
        "Federal Highway Administration",
        "National Highway Traffic Safety Administration",
    ]


def _workspace_dir() -> Path:
    default = Path(__file__).resolve().parent.parent / "workspace"
    workspace = Path(os.getenv("DOT_AGENT_WORKSPACE", default)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _api_headers() -> dict[str, str]:
    return {"X-Api-Key": os.getenv("DATAGOV_API_KEY", "DEMO_KEY")}


def _parse_query(query: str) -> tuple[str, str | None]:
    """Parse query string into search text and optional org_slug."""
    query = (query or "").strip()
    if query.startswith("organization:"):
        slug = query.split(":", 1)[1].strip() or DEFAULT_ORG_SLUG
        return "*", ORG_SLUG_ALIASES.get(slug, slug)
    if "org_slug:" in query:
        parts = query.split()
        org_slug = None
        terms = []
        for part in parts:
            if part.startswith("org_slug:"):
                org_slug = part.split(":", 1)[1]
            else:
                terms.append(part)
        return " ".join(terms) or "*", org_slug
    return query or "*", DEFAULT_ORG_SLUG


def _normalize_datagov_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": (item.get("title") or "").strip(),
        "name": item.get("identifier") or item.get("id"),
        "identifier": item.get("identifier") or item.get("id"),
        "organization": item.get("publisher"),
        "notes": (item.get("description") or "")[:500],
        "tags": item.get("keyword") or [],
        "modified": item.get("last_harvested_date") or item.get("modified"),
        "landing_page": item.get("landingPage") or item.get("landing_page"),
        "resource_urls": [
            url
            for url in [
                item.get("landingPage"),
                item.get("landing_page"),
                item.get("downloadURL"),
            ]
            if url
        ],
        "source": "catalog.data.gov",
    }


def _normalize_ckan_package(pkg: dict[str, Any]) -> dict[str, Any]:
    resources = pkg.get("resources") or []
    return {
        "title": (pkg.get("title") or "").strip(),
        "name": pkg.get("name"),
        "identifier": pkg.get("id"),
        "organization": (pkg.get("organization") or {}).get("title"),
        "notes": (pkg.get("notes") or "")[:500],
        "tags": [t.get("name") for t in (pkg.get("tags") or []) if t.get("name")],
        "modified": pkg.get("metadata_modified") or pkg.get("metadata_created"),
        "landing_page": f"https://catalog.data.gov/dataset/{pkg.get('name')}",
        "resource_urls": [r.get("url") for r in resources if r.get("url")],
        "source": "catalog.data.gov",
    }


def search_datagov_api(
    query: str = "*",
    org_slug: str | None = DEFAULT_ORG_SLUG,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search the Data.gov Catalog API with pagination. Returns (results, error)."""
    all_results: list[dict[str, Any]] = []
    after: str | None = None
    error: str | None = None

    while True:
        params: dict[str, Any] = {"per_page": per_page}
        if query and query not in {"*", "*:*"}:
            params["q"] = query
        if org_slug:
            params["org_slug"] = org_slug
        if after:
            params["after"] = after

        response = requests.get(
            f"{DATAGOV_API_BASE}/search",
            params=params,
            headers=_api_headers(),
            timeout=60,
        )
        if response.status_code == 429:
            error = "Data.gov API rate limit exceeded. Set DATAGOV_API_KEY in .env."
            break
        if not response.ok:
            error = f"Data.gov API HTTP {response.status_code}"
            break

        payload = response.json()
        if payload.get("error"):
            error = payload["error"].get("message", str(payload["error"]))
            break

        results = payload.get("results") or []
        if not results:
            break

        all_results.extend(_normalize_datagov_result(item) for item in results)
        after = payload.get("after")
        if not after:
            break

    return all_results, error


def search_ckan_packages(
    query: str = "*:*",
    fq: str | None = "organization:dot",
    rows: int = 100,
    start: int = 0,
) -> list[dict[str, Any]]:
    """Paginate legacy CKAN package_search results."""
    all_results: list[dict[str, Any]] = []
    current_start = start

    while True:
        params: dict[str, Any] = {"q": query, "rows": rows, "start": current_start}
        if fq:
            params["fq"] = fq

        response = requests.get(CKAN_SEARCH_URL, params=params, timeout=60)
        if response.status_code in {404, 429}:
            break
        if not response.ok:
            break
        payload = response.json()
        if not payload.get("success"):
            break

        result = payload.get("result", {})
        packages = result.get("results", [])
        if not packages:
            break

        all_results.extend(_normalize_ckan_package(pkg) for pkg in packages)

        count = result.get("count", 0)
        current_start += rows
        if current_start >= count:
            break

    return all_results


def fetch_socrata_cross_portal_reference(
    queries: list[str] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Find DOT-related datasets on other Socrata portals via Discovery API."""
    queries = queries or _cross_portal_queries()
    seen_ids: set[str] = set()
    external: list[dict[str, Any]] = []

    for query in queries:
        scroll_id: str | None = "0"
        pages = 0
        max_pages = 3

        while pages < max_pages:
            params: dict[str, Any] = {"q": query, "only": "dataset", "limit": limit}
            if scroll_id is not None:
                params["scroll_id"] = scroll_id

            response = requests.get(SOCRATA_DISCOVERY_URL, params=params, timeout=120)
            if not response.ok:
                break

            results = response.json().get("results") or []
            if not results:
                break

            for item in results:
                parsed = parse_socrata_discovery_entry(item)
                uid = (parsed.get("socrata_uid") or "").lower()
                domain = (parsed.get("domain") or "").lower()

                if domain == DOT_DOMAIN:
                    continue
                if not uid or uid in seen_ids:
                    continue

                seen_ids.add(uid)
                parsed["source"] = "socrata_discovery_cross_portal"
                parsed["discovery_query"] = query
                external.append(parsed)

            pages += 1
            if len(results) < limit:
                break
            last_id = (results[-1].get("resource") or {}).get("id")
            if not last_id:
                break
            scroll_id = last_id

    return external


def _merge_reference_datasets(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate reference datasets by identifier, URL, or title."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = "|".join(
                [
                    (item.get("identifier") or "").lower(),
                    (item.get("landing_page") or "").lower().rstrip("/"),
                    (item.get("title") or "").lower(),
                ]
            )
            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def search_data_gov_packages(query: str = "organization:dot") -> tuple[list[dict[str, Any]], str]:
    """Search reference catalogs. Returns (datasets, source_label)."""
    search_text, org_slug = _parse_query(query)
    source_parts: list[str] = []
    groups: list[list[dict[str, Any]]] = []

    results, api_error = search_datagov_api(query=search_text, org_slug=org_slug)
    if results:
        groups.append(results)
        source_parts.append("catalog.data.gov")
    elif api_error:
        ckan = search_ckan_packages(
            query=search_text if search_text != "*" else "*:*",
            fq=f"organization:{org_slug}",
        )
        if ckan:
            groups.append(ckan)
            source_parts.append("catalog.data.gov (ckan)")

    external, external_sources = fetch_external_reference_catalog()
    if external:
        groups.append(external)
        source_parts.extend(external_sources)

    if not any(g for g in groups if g):
        cross_portal = fetch_socrata_cross_portal_reference()
        if cross_portal:
            groups.append(cross_portal)
            source_parts.append("socrata_discovery_cross_portal")

    merged = _merge_reference_datasets(*groups)
    if merged:
        return merged, " + ".join(source_parts) if source_parts else "combined"

    return [], api_error or "no reference datasets found"


def fetch_and_save_reference_catalog(query: str = "organization:dot") -> tuple[list[dict[str, Any]], Path, str]:
    """Fetch reference datasets and save to workspace."""
    parsed, source = search_data_gov_packages(query=query)
    data_dir = _workspace_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = data_dir / "reference_catalog.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "source": source,
                "dataset_count": len(parsed),
                "datasets": parsed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return parsed, snapshot_path, source


@tool(parse_docstring=True)
def search_data_gov(query: str = "organization:dot") -> str:
    """Search for DOT-related public datasets beyond the DOT portal.

    Tries catalog.data.gov (org slug: dot), then merges supplemental sources from
    data/reference/external_catalogs.yaml: static portals/clearinghouses, registry
    fetchers (MobilityData GBFS, OMF MDS, WZDx feeds, Transitland Atlas US),
    ArcGIS hubs (USDOT NTAD, BTS geodata), known API endpoints, and Socrata
    Discovery cross-portal queries. Saves workspace/data/reference_catalog.json.

    Args:
        query: Search query or organization filter (e.g. organization:dot).

    Returns:
        JSON string with result count, source used, and sample datasets.
    """
    try:
        parsed, snapshot_path, source = fetch_and_save_reference_catalog(query=query)
        summary = {
            "status": "ok" if parsed else "empty",
            "query": query,
            "source": source,
            "dataset_count": len(parsed),
            "snapshot_path": str(snapshot_path),
            "sample": parsed[:5],
            "hint": (
                "Set DATAGOV_API_KEY in .env for full catalog.data.gov coverage"
                if not parsed or source == "socrata_discovery_cross_portal"
                else None
            ),
        }
        return json.dumps(summary, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc), "query": query})
