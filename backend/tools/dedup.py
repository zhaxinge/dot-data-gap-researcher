"""Detect duplicate or near-duplicate datasets in the DOT portal catalog."""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

DUPLICATE_THRESHOLD = 90


def find_duplicate_clusters(
    datasets: list[dict[str, Any]],
    *,
    threshold: int = DUPLICATE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Find fuzzy title duplicate clusters. Returns list of {canonical_title, duplicates[]}."""
    clusters: list[dict[str, Any]] = []
    assigned: set[int] = set()

    for i, ds in enumerate(datasets):
        if i in assigned:
            continue
        title_i = (ds.get("title") or "").strip()
        if not title_i:
            continue

        group = [{"index": i, "title": title_i, "socrata_uid": ds.get("socrata_uid")}]
        assigned.add(i)

        for j in range(i + 1, len(datasets)):
            if j in assigned:
                continue
            title_j = (datasets[j].get("title") or "").strip()
            if not title_j:
                continue
            score = fuzz.token_sort_ratio(title_i.lower(), title_j.lower())
            if score >= threshold:
                group.append(
                    {
                        "index": j,
                        "title": title_j,
                        "socrata_uid": datasets[j].get("socrata_uid"),
                        "similarity": score,
                    }
                )
                assigned.add(j)

        if len(group) > 1:
            clusters.append({"canonical_title": title_i, "members": group})

    return clusters


def annotate_duplicates(
    gaps: list[dict[str, Any]],
    dot_datasets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add duplicate_of to quality gaps when title matches a cluster member."""
    clusters = find_duplicate_clusters(dot_datasets)
    title_to_canonical: dict[str, str] = {}
    for cluster in clusters:
        canonical = cluster["canonical_title"]
        for member in cluster["members"]:
            title_to_canonical[(member.get("title") or "").lower()] = canonical

    updated: list[dict[str, Any]] = []
    for gap in gaps:
        g = dict(gap)
        title = (g.get("title") or "").lower()
        if title in title_to_canonical and title_to_canonical[title].lower() != title:
            g["duplicate_of"] = title_to_canonical[title]
        updated.append(g)
    return updated
