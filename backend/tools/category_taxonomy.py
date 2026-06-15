"""Canonical portal category normalization."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REFERENCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference"


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, Any]:
    path = REFERENCE_DIR / "category_taxonomy.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    """Map lowercase alias -> canonical display name."""
    mapping: dict[str, str] = {}
    for entry in _load_taxonomy().get("canonical") or []:
        name = entry.get("name") or ""
        if not name:
            continue
        for alias in [name, *(entry.get("aliases") or [])]:
            key = alias.strip().lower()
            if key:
                mapping[key] = name
    return mapping


def canonical_category_names() -> list[str]:
    """Ordered list of canonical category display names."""
    return [
        (e.get("name") or "").strip()
        for e in (_load_taxonomy().get("canonical") or [])
        if e.get("name")
    ]


def default_category() -> str:
    return _load_taxonomy().get("default_category") or "Cross-Modal / Other"


def normalize_category(raw: str | None) -> str:
    """Map a raw Socrata or keyword category to a canonical name."""
    if not raw or not str(raw).strip():
        return default_category()
    key = str(raw).strip().lower()
    return _alias_map().get(key, str(raw).strip())
