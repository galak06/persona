# pyright: reportMissingImports=false
"""Top-level aggregator for ``/api/v1/flows/state``.

Six flows surface to the web UI. Each per-flow reader lives in
``api.flow_helpers`` so this module stays small. Schedule entries come
from ``api.schedule_state``.

Readers MUST NOT raise — when one does, ``collect_flow_states`` logs
the failure and emits a fallback ``last_status="never"`` row so the
aggregator never breaks the endpoint.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from api.flow_helpers import (
    read_blog_campaign,
    read_brand_campaigns,
    read_community_growth,
    read_content_ideas,
    read_engagement_comment,
    read_market_intel,
    read_social_loyalty,
)
from api.schedule_state import collect_schedule_state

_log = logging.getLogger("approval_api.flow_state")

_READERS: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
    ("engagement-comment", "Engagement Comment Flow", read_engagement_comment),
    ("blog-campaign", "Blog & Campaign Pipeline", read_blog_campaign),
    ("brand-campaigns", "Brand Campaigns", read_brand_campaigns),
    ("community-growth", "Community Growth Flow", read_community_growth),
    ("social-loyalty", "Social Loyalty & Outreach", read_social_loyalty),
    ("market-intel", "Market Intelligence & Trends", read_market_intel),
    ("content-ideas", "Content Ideas", read_content_ideas),
]


def _fallback(flow_id: str, name: str) -> dict[str, Any]:
    return {
        "id": flow_id,
        "name": name,
        "last_run_at": None,
        "last_status": "never",
        "error_message": None,
        "output_counts": {},
        "sample": [],
    }


def collect_flow_states() -> list[dict[str, Any]]:
    """Run every per-flow reader, swallowing exceptions so a single bad
    state file never breaks the endpoint."""
    out: list[dict[str, Any]] = []
    for flow_id, name, reader in _READERS:
        start = time.perf_counter()
        try:
            result = reader()
        except Exception as exc:  # noqa: BLE001 - readers must never raise upward
            _log.warning("flow reader %s failed: %s", flow_id, exc)
            result = _fallback(flow_id, name)
        elapsed_ms = (time.perf_counter() - start) * 1000
        _log.info(
            '{"event": "flow_reader", "id": "%s", "ms": %.1f}',
            flow_id, elapsed_ms,
        )
        out.append(result)
    return out


__all__ = ["collect_flow_states", "collect_schedule_state"]
