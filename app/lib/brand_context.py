"""Brand identity seam: who this process is running as, and where that brand lives.

Before this module, brand identity had no owner. `lib/config.py` resolved
`BRAND_DIR` at *import* time, so importing anything under `lib` required a
provisioned brand and mutated `os.environ` as a side effect; ~52 other sites
then re-derived the same answer under five different rules. That import-time
side effect is how the live Postgres was wiped on 2026-08-12 (see
`tests/conftest.py`), and it is why `lib/dedup_pg.py` could sit on a hardcoded
brand for months without anything noticing.

`BrandContext` is that owner. Two constructors, deliberately distinct:

    BrandContext.from_env()          -- this process's brand, from BRAND_DIR.
                                        Strict: raises when unset, exactly as
                                        `load_config()` always has.
    BrandContext.for_brand("slug")   -- any registered brand, from the `brands`
                                        table.

`for_brand()` has no production caller today and that is intentional, not dead
code: one process serves one brand (the cron/worker model), but the *interface*
must not foreclose serving several. See `docs/adr/0005-brand-context.md`.
`default_brand_dir()` stays a separate, explicitly-named best-effort fallback so
the strict rule and the guessing rule can never be confused for each other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


class BrandNotFoundError(LookupError):
    """Raised when `brand_id` does not match any row in `brands`."""


class BrandDirNotSetError(LookupError):
    """Raised when the brand exists but has no `brand_dir` provisioned yet."""


class BrandPaths(BaseModel):
    """Every brand-scoped path, derived once from `brand_dir`.

    Lives here rather than in `lib/config.py` so the dependency points one
    way: `config` imports `brand_context`, never the reverse. Re-exported from
    `lib.config` for the callers that already import it from there.
    """

    brand_dir: Path
    data_dir: Path
    state_dir: Path
    logs_dir: Path
    backups_dir: Path
    campaigns_dir: Path
    schedule_file: Path

    # Specific file paths within brand
    brand_voice_guide: Path
    campaigns: Path
    citation_sources: Path
    competitors: Path
    content_rules: Path
    groups_tracker: Path
    instagram_accounts: Path
    keyword_clusters: Path
    post_templates: Path
    recipe_products: Path

    # State paths
    comment_queue: Path  # legacy shared queue — retained for migration/back-compat
    instagram_comment_queue: Path  # IG loop owns its own queue
    facebook_comment_queue: Path  # FB loop owns its own queue
    ideator_queue: Path
    campaign_verify_queue: Path
    dedup_cache: Path
    rate_limit_tracker: Path
    last_run: Path
    facebook_session: Path
    instagram_session: Path
    tiktok_session: Path
    pending_groups: Path


def default_brand_dir() -> Path:
    """Best-effort `app/brands/<brand>` guess. Never raises.

    Prefers `PERSONA_BRAND`; otherwise, if exactly one brand folder is present,
    uses it. Some callers evaluate this at import time, which is why it must not
    raise — and precisely why it is kept separate from `BrandContext.from_env()`,
    which is strict and always requires the env var.
    """
    brands_root = Path(__file__).resolve().parent.parent / "brands"
    brand = os.environ.get("PERSONA_BRAND")
    if brand:
        return brands_root / brand
    candidates = sorted(
        d
        for d in brands_root.glob("*")
        if d.is_dir() and not d.name.startswith((".", "_")) and ".bak" not in d.name
    )
    return candidates[0] if len(candidates) == 1 else brands_root


def resolve_paths(brand_dir: Path) -> BrandPaths:
    """Derive the full path tree from a brand directory."""
    data_dir = brand_dir / "data"
    state_dir = brand_dir / "state"
    logs_dir = brand_dir / "logs"

    return BrandPaths(
        brand_dir=brand_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        logs_dir=logs_dir,
        backups_dir=brand_dir / "backups",
        campaigns_dir=brand_dir / "campaigns",
        schedule_file=brand_dir / "schedule.json",
        brand_voice_guide=data_dir / "config" / "brand_voice_guide.md",
        campaigns=data_dir / "config" / "campaigns.json",
        citation_sources=data_dir / "config" / "citation_sources.json",
        competitors=data_dir / "config" / "competitors.json",
        content_rules=data_dir / "config" / "content_rules.json",
        groups_tracker=data_dir / "trackers" / "groups_tracker.json",
        instagram_accounts=data_dir / "config" / "instagram_accounts.csv",
        keyword_clusters=data_dir / "config" / "keyword_clusters.json",
        post_templates=data_dir / "config" / "post_templates.json",
        recipe_products=data_dir / "config" / "recipe_products.json",
        comment_queue=state_dir / "comment_queue.json",
        instagram_comment_queue=state_dir / "instagram_comment_queue.json",
        facebook_comment_queue=state_dir / "facebook_comment_queue.json",
        ideator_queue=state_dir / "ideator_queue.json",
        campaign_verify_queue=state_dir / "campaign_verify_queue.json",
        dedup_cache=state_dir / "dedup_cache.json",
        rate_limit_tracker=state_dir / "rate_limit_tracker.json",
        last_run=state_dir / "last_run.json",
        facebook_session=state_dir / "facebook_session.json",
        instagram_session=state_dir / "instagram_session.json",
        tiktok_session=state_dir / "tiktok_session.json",
        pending_groups=state_dir / "pending_groups.json",
    )


@dataclass(frozen=True)
class BrandContext:
    """The brand a unit of work runs as: its id, its directory, its paths.

    Frozen because a context is an answer, not a mutable setting. Code that
    needs a different brand constructs a different context — which is what
    keeps the door open to serving several without rewriting callers.
    """

    brand_id: str
    brand_dir: Path
    paths: BrandPaths

    @classmethod
    def _build(cls, brand_dir: Path, brand_id: str | None = None) -> BrandContext:
        resolved = brand_dir.resolve()
        return cls(
            brand_id=brand_id or os.environ.get("PERSONA_BRAND", "").strip() or resolved.name,
            brand_dir=resolved,
            paths=resolve_paths(resolved),
        )

    @classmethod
    def from_env(cls) -> BrandContext:
        """This process's brand, from `BRAND_DIR`.

        Keeps `load_config()`'s original failure mode verbatim: a clear error
        when the variable is unset, rather than a guess. Callers that genuinely
        want a guess ask `default_brand_dir()` by name.
        """
        brand_dir_str = os.environ.get("BRAND_DIR")
        if not brand_dir_str:
            raise ValueError(
                "BRAND_DIR environment variable is not set. "
                "Please set it to the path of the brand configuration directory."
            )
        return cls._build(Path(brand_dir_str))

    @classmethod
    def for_brand(cls, brand_id: str) -> BrandContext:
        """Any registered brand, resolved through the `brands` table.

        The registered `brand_dir` is the only path two containers agree on —
        the API's own `BRAND_DIR` names a directory the worker may not have.
        """
        from lib.brands_db.repository import BrandsRepository

        row = BrandsRepository().get(brand_id)
        if row is None:
            raise BrandNotFoundError(f"no brand registered with id '{brand_id}'")

        brand_dir = row.get("brand_dir") or ""
        if not brand_dir:
            raise BrandDirNotSetError(f"brand '{brand_id}' has no brand_dir set yet")
        return cls._build(Path(brand_dir), brand_id=brand_id)

    def load_env(self, *, apply_secrets: bool = False) -> int:
        """Merge this brand's `.env` and the shared settings file into os.environ.

        Was `lib/config.py`'s module-level bootstrap. Moving it behind the seam
        is the point of this module: the merge is now something a caller *does*,
        at a moment it chooses, rather than something that happens to it on
        import.

        `apply_secrets=False` by default — the encrypted overlay opens a DB
        connection (a ~10s pool timeout when Postgres is down), and this runs on
        the path to `settings`, which nearly everything touches. Entrypoints
        that publish call `load_brand_env_into_environ(apply_secrets=True)`
        themselves and get the overlay there.
        """
        from lib.local_env import load_brand_env_into_environ, load_local_env

        loaded = load_brand_env_into_environ(self.brand_dir, apply_secrets=apply_secrets)
        return loaded + load_local_env()


def current_brand_id() -> str:
    """This process's brand id, best-effort — never raises.

    For the modules that only need the *name* (dedup rows, Redis key scopes,
    secrets lookups) and previously each re-derived it inline, or worse,
    hardcoded it. Prefers `PERSONA_BRAND`, then the `BRAND_DIR` folder name,
    then `"default"`.
    """
    explicit = os.environ.get("PERSONA_BRAND", "").strip()
    if explicit:
        return explicit
    brand_dir = os.environ.get("BRAND_DIR", "").strip()
    if brand_dir:
        return Path(brand_dir).name
    return "default"
