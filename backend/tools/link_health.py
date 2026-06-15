"""Optional HTTP link health checks for gap landing pages."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT = 10
DEFAULT_DELAY_MS = 100
AUTH_DOMAINS = frozenset(
    {
        "login.microsoftonline.com",
        "auth.",
        "edge",
        "cdn.",
    }
)


def check_url_status(url: str | None, *, timeout: int = DEFAULT_TIMEOUT) -> str:
    """HEAD/GET a URL and return a short status label."""
    if not url or not str(url).strip():
        return "no_url"

    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        return "invalid_url"

    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code >= 400:
            response = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
        status = response.status_code
        final_host = (urlparse(response.url).netloc or "").lower()

        if status == 404:
            return "404"
        if status >= 500:
            return f"error_{status}"
        if status >= 400:
            return f"client_{status}"
        if any(marker in final_host for marker in AUTH_DOMAINS):
            return "auth_redirect"
        if response.url.rstrip("/") != url.rstrip("/"):
            return f"redirect_{status}"
        return f"ok_{status}"
    except requests.Timeout:
        return "timeout"
    except Exception:
        return "unreachable"


def enrich_gaps_link_health(
    gaps: list[dict[str, Any]],
    *,
    max_checks: int = 50,
    delay_ms: int = DEFAULT_DELAY_MS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Check landing_page for top-priority gaps. Returns (updated_gaps, stats)."""
    stats = {"checked": 0, "ok": 0, "failed": 0}
    candidates = [
        g
        for g in gaps
        if g.get("landing_page") and g.get("status") in ("missing_on_portal", "redirect_only")
    ]
    candidates = sorted(candidates, key=lambda g: g.get("priority_score", 0), reverse=True)[:max_checks]
    check_ids = {id(g) for g in candidates}

    updated: list[dict[str, Any]] = []
    for gap in gaps:
        g = dict(gap)
        if id(gap) in check_ids:
            status = check_url_status(g.get("landing_page"))
            g["link_status"] = status
            stats["checked"] += 1
            if status.startswith("ok"):
                stats["ok"] += 1
            else:
                stats["failed"] += 1
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        updated.append(g)

    return updated, stats
