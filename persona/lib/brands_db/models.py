"""Data contracts for the brands DB layer.

Mirrors `groups_db/models.py`'s shape: allowed values as string constants on
a small namespace class, plus a helper for the one computed default
(`enabled_flows`) callers need without duplicating the literal list.
"""

from __future__ import annotations


class BrandStatus:
    """Allowed values for `brands.status` (the onboarding lifecycle)."""

    DRAFT: str = "draft"
    PROVISIONING: str = "provisioning"
    PROVISIONED: str = "provisioned"
    ACTIVE: str = "active"
    DISABLED: str = "disabled"

    ALL: frozenset[str] = frozenset({"draft", "provisioning", "provisioned", "active", "disabled"})


def default_enabled_flows() -> list[str]:
    """Default `brands.enabled_flows` for a newly onboarded brand.

    Stage 1 scope is intentionally narrow -- only the two engagement scanners,
    no WP posting, no recipe pipeline (see the plan's Stage 1 goal).
    """
    return ["ig-scanner", "fb-scanner"]


# Every flow id `enabled_flows` can ever govern -- the full set
# `lib.brand_provisioning._STAGE1_FLOWS` knows how to provision. Used by
# `scripts/task_dispatcher.py` to scope its `enabled_flows` gate: a
# `schedule_tasks` row whose flow id isn't in this set belongs to some other,
# non-onboarding-managed flow (e.g. a legacy WP/recipe schedule) and must
# never be silently skipped just because it's absent from `enabled_flows`.
MANAGED_FLOW_IDS: frozenset[str] = frozenset({"ig-scanner", "fb-scanner", "fb-group-scout"})


# Dict keys that map to typed `brands` columns beyond the primary key. Kept
# here (rather than inline in the repository) so `create()`'s INSERT column
# list and any future validation/serialization code share one source.
BRAND_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "persona",
    "site_url",
    "niche",
    "mascot_name",
    "target_audience",
    "keywords",
    "competitor_accounts",
    "enabled_flows",
    "headless",
    "group_join_limit",
    "status",
    "brand_dir",
    "extra",
)
