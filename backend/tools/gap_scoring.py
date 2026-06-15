"""Priority scoring for gap records (Nova Toe / DAT interview sorting)."""

from __future__ import annotations

from typing import Any

HIGH_VALUE_SOURCES = frozenset(
    {
        "catalog.data.gov",
        "catalog.data.gov (ckan)",
    }
)

RESTRICTED_ACCESS = frozenset(
    {
        "agreement_required",
        "sponsorship_required",
        "permissioned",
        "commercial_license",
        "api_key_required",
        "registration_required",
    }
)

DATA_ASSET_TYPES = frozenset({"api_dataset", "downloadable_data"})

FEDERAL_PROVIDERS = frozenset(
    {"BTS", "FHWA", "FRA", "FTA", "FAA", "NHTSA", "FMCSA", "PHMSA", "MARAD", "USDOT"}
)


def priority_score(gap: dict[str, Any]) -> int:
    """Score a gap for sort order (higher = more important to address)."""
    score = 0
    gap_class = (gap.get("gap_class") or "").lower()
    source = (gap.get("source") or "").lower()
    access = (gap.get("access") or "public").lower()
    asset_type = (gap.get("asset_type") or "").lower()
    status = (gap.get("status") or "").lower()

    if gap_class == "true_gap":
        score += 40
    elif gap_class == "intentional_external":
        score -= 50

    if source in HIGH_VALUE_SOURCES:
        score += 30
    elif source.startswith("arcgis_hub"):
        score += 25
    elif source.startswith("socrata_discovery"):
        score += 10

    if asset_type in DATA_ASSET_TYPES:
        score += 20

    if gap.get("landing_page"):
        score += 10

    if (gap.get("provider") or "") in FEDERAL_PROVIDERS and gap_class == "true_gap":
        score += 20

    if access in RESTRICTED_ACCESS:
        score -= 30

    if source.startswith("registry:") or source.startswith("external_catalog_clearinghouse"):
        score -= 20

    if status == "stale_metadata":
        score += 15
    elif status == "no_api_endpoint":
        score += 10
    elif status == "redirect_only":
        score += 5

    return score


def sort_gaps_by_priority(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return gaps sorted by priority_score descending."""
    enriched = []
    for gap in gaps:
        g = dict(gap)
        g["priority_score"] = priority_score(g)
        enriched.append(g)
    return sorted(enriched, key=lambda g: g.get("priority_score", 0), reverse=True)
