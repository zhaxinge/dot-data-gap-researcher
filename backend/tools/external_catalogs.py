"""Load and fetch supplemental transportation data catalog sources."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import requests
import yaml

from tools.dot_catalog import DOT_DOMAIN, SOCRATA_DISCOVERY_URL, parse_socrata_discovery_entry

REFERENCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference"

GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_API = "https://api.github.com"


def load_external_catalogs() -> dict[str, Any]:
    path = REFERENCE_DIR / "external_catalogs.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _entry_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            (item.get("identifier") or "").lower(),
            (item.get("landing_page") or "").lower().rstrip("/"),
            (item.get("title") or "").lower(),
        ]
    )


def _collect_urls(item: dict[str, Any]) -> list[str]:
    keys = (
        "url",
        "developer_url",
        "api_url",
        "catalog_api",
        "catalog_url",
        "archive_url",
        "docs_url",
        "github_url",
    )
    return [item[k] for k in keys if item.get(k)]


def _static_section(config: dict[str, Any], section: str, source_label: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in config.get(section) or []:
        title = item.get("name") or item.get("title")
        if not title:
            continue
        entries.append(
            {
                "title": title,
                "identifier": item.get("id"),
                "organization": item.get("bureau"),
                "notes": item.get("notes"),
                "landing_page": item.get("url") or item.get("catalog_url") or item.get("github_url"),
                "resource_urls": _collect_urls(item),
                "tags": [item.get("modal")] if item.get("modal") else [],
                "source": source_label,
                "access": item.get("access", "public"),
            }
        )
    return entries


def static_reference_entries() -> list[dict[str, Any]]:
    """Convert YAML portal, API, clearinghouse, and standards entries into reference records."""
    config = load_external_catalogs()
    entries: list[dict[str, Any]] = []
    entries.extend(_static_section(config, "portals", "external_catalog_portal"))
    entries.extend(_static_section(config, "clearinghouses", "external_catalog_clearinghouse"))
    entries.extend(_static_section(config, "standards_registries", "external_catalog_standards"))

    for api in config.get("api_endpoints") or []:
        entries.append(
            {
                "title": api.get("title"),
                "identifier": api.get("id"),
                "organization": api.get("bureau"),
                "notes": api.get("notes"),
                "landing_page": api.get("docs_url") or api.get("url"),
                "resource_urls": _collect_urls(api),
                "tags": [api.get("modal")] if api.get("modal") else [],
                "source": "external_catalog_api",
                "access": api.get("access", "public"),
                "format": api.get("format"),
            }
        )

    return [e for e in entries if e.get("title")]


def _parse_csv_registry(
    text: str,
    fetcher: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    """Parse a CSV registry into reference dataset records."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    title_field = fetcher.get("title_field")
    url_field = fetcher.get("url_field")
    id_field = fetcher.get("id_field")
    country_filter = fetcher.get("country_filter")

    results: list[dict[str, Any]] = []
    for row in reader:
        if country_filter:
            country = (row.get("Country Code") or row.get("country_code") or "").strip().upper()
            if country and country != country_filter.upper():
                continue

        title = (row.get(title_field) or "").strip() if title_field else ""
        if not title:
            continue

        landing = (row.get(url_field) or "").strip() if url_field else ""
        ident = (row.get(id_field) or title).strip() if id_field else title

        results.append(
            {
                "title": title,
                "identifier": f"{source_id}:{ident}",
                "organization": fetcher.get("bureau"),
                "landing_page": landing or fetcher.get("url"),
                "resource_urls": [u for u in [landing, fetcher.get("url")] if u],
                "tags": [fetcher.get("modal")] if fetcher.get("modal") else [],
                "source": f"registry:{source_id}",
                "registry_name": fetcher.get("name"),
            }
        )

    return results


def fetch_csv_registry(fetcher: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and parse a remote CSV registry."""
    url = fetcher.get("url")
    if not url:
        return []
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return _parse_csv_registry(response.text, fetcher, fetcher.get("id", "csv"))


def fetch_wzdx_registry(fetcher: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch WZDx feed registry CSV (jurisdiction-level work zone API endpoints)."""
    try:
        entries = fetch_csv_registry(fetcher)
        for entry in entries:
            entry["notes"] = (
                "WZDx jurisdiction feed — operational work-zone API, "
                "typically not indexed as a dataset row on data.transportation.gov"
            )
        return entries
    except Exception:
        return []


def _is_likely_us_dmfr(filename: str, exclude_patterns: list[str]) -> bool:
    lowered = filename.lower()
    if not lowered.endswith(".dmfr.json"):
        return False
    return not any(pattern in lowered for pattern in exclude_patterns)


def fetch_transitland_atlas_us(fetcher: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch US feed entries from Transitland Atlas DMFR files on GitHub."""
    repo = fetcher.get("github_repo", "transitland/transitland-atlas")
    path = fetcher.get("github_path", "feeds")
    exclude_patterns = [p.lower() for p in (fetcher.get("exclude_patterns") or [])]
    max_files = int(fetcher.get("max_files", 50))

    api_url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    all_files: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        response = requests.get(
            api_url,
            params={"per_page": 100, "page": page},
            headers={"Accept": "application/vnd.github+json"},
            timeout=60,
        )
        if not response.ok:
            break
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        all_files.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    results: list[dict[str, Any]] = []
    files = [
        f
        for f in all_files
        if _is_likely_us_dmfr(f.get("name", ""), exclude_patterns)
    ]
    files = files[:max_files]

    for file_meta in files:
        download_url = file_meta.get("download_url")
        if not download_url:
            continue
        try:
            feed_resp = requests.get(download_url, timeout=60)
            if not feed_resp.ok:
                continue
            dmfr = feed_resp.json()
            for feed in dmfr.get("feeds") or []:
                urls = feed.get("urls") or {}
                static_url = urls.get("static_current") or urls.get("realtime_vehicle_positions")
                if isinstance(static_url, list):
                    static_url = static_url[0] if static_url else None
                title = feed.get("id") or file_meta.get("name", "")
                results.append(
                    {
                        "title": f"Transitland feed: {title}",
                        "identifier": feed.get("id"),
                        "organization": fetcher.get("bureau"),
                        "landing_page": static_url or file_meta.get("html_url"),
                        "resource_urls": [u for u in [static_url, file_meta.get("html_url")] if u],
                        "tags": [feed.get("spec"), fetcher.get("modal")],
                        "source": "registry:transitland_atlas_us",
                        "registry_name": fetcher.get("name"),
                    }
                )
        except Exception:
            continue

    return results


def fetch_registry_fetchers() -> list[dict[str, Any]]:
    """Run all configured registry fetchers from external_catalogs.yaml."""
    config = load_external_catalogs()
    all_results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for fetcher in config.get("registry_fetchers") or []:
        fetcher_id = fetcher.get("id", "unknown")
        fmt = fetcher.get("format", "csv")

        try:
            if fmt == "csv":
                if fetcher_id == "wzdx_feed_registry":
                    items = fetch_wzdx_registry(fetcher)
                else:
                    items = fetch_csv_registry(fetcher)
            elif fmt == "dmfr_json":
                items = fetch_transitland_atlas_us(fetcher)
            else:
                items = []
        except Exception:
            items = []

        for item in items:
            key = _entry_key(item)
            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            all_results.append(item)

    return all_results


def fetch_arcgis_hub_catalog(
    catalog_api: str,
    hub_id: str,
    hub_name: str,
    bureau: str = "",
    max_pages: int = 5,
    page_size: int = 99,
) -> list[dict[str, Any]]:
    """Paginate an ArcGIS Open Data Hub v3 datasets API."""
    results: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        response = requests.get(
            catalog_api,
            params={"page[size]": page_size, "page[number]": page},
            timeout=120,
        )
        if not response.ok:
            break

        payload = response.json()
        data = payload.get("data") or []
        if not data:
            break

        for item in data:
            attrs = item.get("attributes") or {}
            links = item.get("links") or {}
            results.append(
                {
                    "title": (attrs.get("name") or attrs.get("title") or "").strip(),
                    "identifier": item.get("id"),
                    "organization": bureau or attrs.get("owner"),
                    "notes": (attrs.get("description") or "")[:500],
                    "modified": attrs.get("updatedAt") or attrs.get("created"),
                    "landing_page": links.get("itemPage") or attrs.get("url"),
                    "resource_urls": [
                        u for u in [links.get("itemPage"), links.get("esriRest"), attrs.get("url")] if u
                    ],
                    "tags": attrs.get("tags") or [],
                    "source": f"arcgis_hub:{hub_id}",
                    "hub_name": hub_name,
                }
            )

        meta = payload.get("meta") or {}
        stats = meta.get("stats") or {}
        page_count = stats.get("pageCount") or 0
        if page >= page_count:
            break

    return results


def fetch_all_arcgis_hubs() -> list[dict[str, Any]]:
    """Fetch datasets from configured ArcGIS Hub catalogs."""
    config = load_external_catalogs()
    all_results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for hub in config.get("arcgis_hubs") or []:
        api = hub.get("catalog_api")
        if not api:
            continue
        try:
            datasets = fetch_arcgis_hub_catalog(
                catalog_api=api,
                hub_id=hub.get("id", "hub"),
                hub_name=hub.get("name", ""),
                bureau=hub.get("bureau", ""),
            )
            for ds in datasets:
                key = (ds.get("identifier") or ds.get("title") or "").lower()
                if key and key not in seen:
                    seen.add(key)
                    all_results.append(ds)
        except Exception:
            continue

    return all_results


def fetch_socrata_discovery_queries(
    queries: list[str] | None = None,
    limit: int = 500,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    """Run Socrata Discovery queries from external_catalogs.yaml."""
    config = load_external_catalogs()
    queries = queries or config.get("socrata_discovery_queries") or []
    seen_ids: set[str] = set()
    external: list[dict[str, Any]] = []

    for query in queries:
        scroll_id: str | None = "0"
        pages = 0

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
                parsed["source"] = "socrata_discovery_external"
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


def fetch_external_reference_catalog() -> tuple[list[dict[str, Any]], list[str]]:
    """Aggregate all supplemental reference sources. Returns (datasets, sources_used)."""
    combined: list[dict[str, Any]] = []
    sources_used: list[str] = []
    seen: set[str] = set()

    def _add(items: list[dict[str, Any]], source_label: str) -> None:
        added = 0
        for item in items:
            key = _entry_key(item)
            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            combined.append(item)
            added += 1
        if added:
            sources_used.append(f"{source_label} ({added})")

    _add(static_reference_entries(), "static_catalogs")
    _add(fetch_registry_fetchers(), "registry_fetchers")
    _add(fetch_all_arcgis_hubs(), "arcgis_hubs")
    _add(fetch_socrata_discovery_queries(), "socrata_discovery")

    return combined, sources_used
