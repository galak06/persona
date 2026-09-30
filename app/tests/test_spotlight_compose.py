"""What one product spotlight composition does -- `lib.crew.spotlight.compose`.

Two properties carry this module. The first is that the row NEVER survives a
run in `'composing'`: an operator watching the review page must be told what
went wrong, and the only channel for that is `content_derivatives.error`, so
every reason in the frozen vocabulary is exercised against `mark_failed` here.
The second is that a failure costs nothing it cannot take back -- no generated
image is left orphaned in the pending directory for a row that never points at
it.

The happy path is asserted on the payload rather than on the return value: the
review card, the DM the operator copies out of it and the worker that publishes
it all read the stored row, so "it composed" means "the row holds two captions,
two channel-tagged affiliate links and an image that exists".
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import ast
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.reference_library import library_root
from lib.crew.spotlight import compose
from lib.crew.spotlight.prompts import spotlight_keyword
from tests._spotlight_fakes import (
    ASIN,
    ASSOCIATES_TAG,
    DERIVATIVE_ID,
    IMAGE,
    MASCOT_NAME,
    POST_SLUG,
    PRODUCT_DISPLAY,
    PRODUCT_KEY,
    build_world,
    composing_row,
    plan_for,
    product_entry,
    published_idea,
    run_compose,
)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return build_world(monkeypatch, tmp_path)


def _pending(tmp_path: Path) -> list[Path]:
    """Every file the composition could have left behind."""
    directory = tmp_path / "state" / "social_posts_pending"
    return sorted(p for p in directory.glob("*") if p.is_file()) if directory.exists() else []


# ── the happy path ──────────────────────────────────────────────────────────


def test_a_composed_spotlight_stores_both_captions_both_links_and_an_image(
    world: dict[str, Any], tmp_path: Path
) -> None:
    result = run_compose(world)

    assert (result.ok, result.reason, result.source) == (True, "composed", "gemini")
    assert result.image_path == IMAGE
    assert (tmp_path / IMAGE).read_bytes() == b"composed:a-generated-image"

    written = world["written"][0]
    assert written["id"] == DERIVATIVE_ID
    assert written["fb_caption"] == world["plan"].fb_caption
    assert written["ig_caption"] == world["plan"].ig_caption
    assert written["comment_keyword"] == "DENTAL"
    assert written["image_path"] == IMAGE
    assert written["image_alt"] == world["plan"].image_alt_text
    assert written["source"] == "gemini"
    assert world["failed"] == []


def test_each_channel_gets_its_own_attributable_affiliate_link(world: dict[str, Any]) -> None:
    """One product, two links: Amazon reports `ascsubtag` in aggregate, so FB
    and IG clicks are only ever told apart by the campaign id carried here."""
    run_compose(world)

    written = world["written"][0]
    base = f"https://www.amazon.com/dp/{ASIN}?tag={ASSOCIATES_TAG}&ascsubtag="
    assert written["fb_affiliate_url"] == f"{base}fb-{POST_SLUG}"
    assert written["ig_affiliate_url"] == f"{base}ig-{POST_SLUG}"


def test_the_crew_runs_under_the_product_keyword_and_the_affiliate_rules(
    world: dict[str, Any],
) -> None:
    """The spotlight's blocking rules ride the writer's OWN retry loop, so a
    draft that drops its disclosure is corrected rather than thrown away."""
    run_compose(world)

    call = world["crew_calls"][0]
    assert call["target_keyword"] == spotlight_keyword(product_entry())
    # The partial is bound to this product: a plan missing the disclosure has
    # to come back as a violation, or the retry loop would never see one.
    violations = call["extra_violations"](plan_for(fb_caption="no disclosure here"))
    assert any("disclosure" in v.lower() for v in violations)
    # The brief the crew was handed is grounded in the article and never leaks
    # the ASIN or a link into a caption the platforms would suppress.
    assert PRODUCT_DISPLAY in call["description"]
    assert ASIN not in call["description"]


# ── a product the article never linked ──────────────────────────────────────


def test_a_product_the_article_never_linked_is_flagged_not_refused(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """The owner may spotlight any catalog product. The post is the grounding,
    not the gate -- otherwise the feature could only repeat the article."""
    world["post"] = {"title": {"rendered": "T"}, "content": {"rendered": "<p>no links here</p>"}}

    result = run_compose(world)

    assert (result.ok, result.reason) == (True, "composed")
    assert world["written"][0]["validation_flags"] == ["product_not_in_post"]
    assert (tmp_path / IMAGE).exists()
    assert world["failed"] == []


def test_flags_come_from_all_three_sources_at_once(world: dict[str, Any]) -> None:
    """Banned claims (both captions), the spotlight's own flag rules, and the
    not-in-post marker are independent scanners; the card shows the union."""
    world["post"] = {"title": {"rendered": "T"}, "content": {"rendered": "<p>nothing</p>"}}
    world["plan"] = plan_for(
        fb_caption=(
            "This chew cures gum disease. "
            f"{MASCOT_NAME} loves her Greenies every night. "
            "As an Amazon Associate I earn from qualifying purchases."
        )
    )

    run_compose(world)

    assert world["written"][0]["validation_flags"] == [
        "cure_claim",
        "mascot_usage_unverified",
        "product_not_in_post",
    ]


def test_a_claim_in_both_captions_is_flagged_once(world: dict[str, Any]) -> None:
    """Two captions, one card: a duplicated flag would read as two problems."""
    world["plan"] = plan_for(
        fb_caption="This chew cures gum disease.", ig_caption="It cures gum disease too."
    )

    run_compose(world)

    assert world["written"][0]["validation_flags"] == ["cure_claim"]


# ── the owner's photo pick ──────────────────────────────────────────────────


def test_the_owners_photo_category_overrides_the_plan_and_reaches_the_resolver(
    world: dict[str, Any],
) -> None:
    """The owner can see the post AND the library; the planner could see
    neither. Their pick wins, and the plan carries it so the image brief and
    the resolved photo describe the same scene."""
    world["row"] = composing_row(reference_category="studio-mascot")
    world["plan"] = plan_for(reference_category="kitchen-counter")

    result = run_compose(world)

    assert result.ok
    resolved = world["resolve_calls"][0]
    assert resolved["requested"] == "studio-mascot"
    assert resolved["planned"] == "studio-mascot"  # forced onto the plan first
    assert world["generate_calls"][0]["reference_image_bytes"] == b"mascot-bytes"


def test_without_an_override_the_plans_own_category_is_used(world: dict[str, Any]) -> None:
    run_compose(world)

    resolved = world["resolve_calls"][0]
    assert (resolved["requested"], resolved["planned"]) == ("", "kitchen-counter")
    assert world["generate_calls"][0]["reference_image_bytes"] == b"kitchen-bytes"


# ── the dry run ─────────────────────────────────────────────────────────────


def test_a_dry_run_drafts_the_captions_but_generates_and_writes_nothing(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """Deliberately AFTER the writer call -- the plan is the thing a dry run
    exists to read -- and deliberately before the image call and the row
    write, which are the spend and the state change."""
    result = run_compose(world, dry_run=True)

    assert (result.ok, result.reason) == (True, "dry_run")
    assert (result.image_path, result.source) == ("", "")
    assert len(world["crew_calls"]) == 1
    assert world["generate_calls"] == []
    assert world["written"] == []
    assert world["failed"] == []
    assert _pending(tmp_path) == []


# ── every way to fail ───────────────────────────────────────────────────────


def _set(**changes: Any) -> Callable[[dict[str, Any], pytest.MonkeyPatch], None]:
    def apply(world: dict[str, Any], _monkeypatch: pytest.MonkeyPatch) -> None:
        world.update(changes)

    return apply


def _drop_tag(_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMAZON_ASSOCIATES_TAG")


def _empty_library(world: dict[str, Any], _monkeypatch: pytest.MonkeyPatch) -> None:
    # Not even a mascot photo left, which is the only way the resolver's third
    # tier comes back empty -- and the only way a spotlight has no reference.
    shutil.rmtree(library_root(world["brand_dir"]))


_FAILURES: tuple[tuple[str, Callable[[dict[str, Any], pytest.MonkeyPatch], None]], ...] = (
    ("not_found", _set(row=None)),
    ("not_composing", _set(row=composing_row(status="queued"))),
    ("idea_not_found", _set(idea=None)),
    ("no_wp_post", _set(idea=published_idea(wp_post_id=""))),
    ("wp_fetch_failed", _set(post=None)),
    ("wp_fetch_failed", _set(wp_error=httpx.ConnectError("wordpress is down"))),
    ("product_not_in_catalog", _set(pool={})),
    ("associates_tag_missing", _drop_tag),
    (
        "affiliate_url_failed",
        _set(pool={PRODUCT_KEY: ProductEntry(key=PRODUCT_KEY, asin="", display=PRODUCT_DISPLAY)}),
    ),
    ("plan_failed", _set(plan=None)),
    ("no_reference_photo", _empty_library),
    ("generation_failed", _set(image=None)),
    ("overlay_failed", _set(overlay_fails=True)),
    ("write_refused", _set(write_ok=False)),
)


@pytest.mark.parametrize(("reason", "arrange"), _FAILURES, ids=[f"{r}" for r, _ in _FAILURES])
def test_every_failure_reaches_the_row_and_leaves_no_orphan_image(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    arrange: Callable[[dict[str, Any], pytest.MonkeyPatch], None],
) -> None:
    """THE property. A `'composing'` row that is never written is invisible to
    the review page until the 20-minute stale sweep calls it a timeout, so the
    operator would be told the wrong thing twenty minutes late."""
    arrange(world, monkeypatch)

    result = run_compose(world)

    assert (result.ok, result.reason) == (False, reason)
    assert world["failed"] == [{"id": DERIVATIVE_ID, "error": reason}]
    assert _pending(tmp_path) == []


def test_a_refused_write_discards_the_image_it_just_generated(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """The row moved under the run (rejected, or swept as stale). The file it
    wrote belongs to nothing now -- keeping it would fill the pending
    directory with images no row will ever point at."""
    world["write_ok"] = False

    result = run_compose(world)

    assert (result.ok, result.reason) == (False, "write_refused")
    assert len(world["written"]) == 1  # it did try
    assert not (tmp_path / IMAGE).exists()


def test_a_row_that_is_not_composing_spends_nothing(world: dict[str, Any]) -> None:
    """Rejected, already composed, or failed by the stale sweep while this run
    queued behind another flow: the status guard is before every paid call."""
    world["row"] = composing_row(status="failed")

    result = run_compose(world)

    assert result.reason == "not_composing"
    assert world["crew_calls"] == []
    assert world["generate_calls"] == []


def _boom(*_a: Any, **_kw: Any) -> None:
    raise RuntimeError("the worker fell over")


def test_an_unexpected_exception_fails_the_row_before_it_propagates(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash must not leave the row wedged at 'composing' either -- and it is
    re-raised so the worker still records the run as an error."""
    monkeypatch.setattr(compose, "load_candidate_pool", _boom)

    with pytest.raises(RuntimeError):
        run_compose(world)

    assert world["failed"] == [{"id": DERIVATIVE_ID, "error": "unexpected_error"}]
    assert world["written"] == []


def test_a_flag_scan_that_raises_leaves_no_image_behind(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reviewer's flags are computed BEFORE anything is generated or
    written. Evaluated as an argument to `set_pending_review` they would leave
    the image orphaned, because the unlink only runs on a REFUSED write."""
    monkeypatch.setattr(compose.inputs, "review_flags", _boom)

    with pytest.raises(RuntimeError):
        run_compose(world)

    assert _pending(tmp_path) == []
    assert world["written"] == []
    assert world["generate_calls"] == []  # not even the image call was paid for
    assert world["failed"] == [{"id": DERIVATIVE_ID, "error": "unexpected_error"}]


# ── a dry run that fails ────────────────────────────────────────────────────
#: Everything a dry run can still reach: it stops at the writer, so no reason
#: from the image half of the run is reachable with `dry_run=True`.
_DRY_RUN_REACHABLE = frozenset(
    {
        "not_found",
        "not_composing",
        "idea_not_found",
        "no_wp_post",
        "wp_fetch_failed",
        "product_not_in_catalog",
        "associates_tag_missing",
        "affiliate_url_failed",
        "plan_failed",
    }
)
_DRY_RUN_FAILURES = tuple(f for f in _FAILURES if f[0] in _DRY_RUN_REACHABLE)


@pytest.mark.parametrize(
    ("reason", "arrange"), _DRY_RUN_FAILURES, ids=[f"{r}" for r, _ in _DRY_RUN_FAILURES]
)
def test_a_dry_run_that_fails_still_changes_no_row(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    arrange: Callable[[dict[str, Any], pytest.MonkeyPatch], None],
) -> None:
    """THE dry-run property, and the whole reason the mode exists: the operator
    previews the captions against a REAL row. A WP 502 on the way there must
    not leave that row terminally 'failed' -- the dispatched run would then
    stop at 'not_composing' and their spotlight would be silently gone."""
    arrange(world, monkeypatch)

    result = run_compose(world, dry_run=True)

    assert (result.ok, result.reason) == (False, reason)
    assert world["failed"] == []
    assert world["written"] == []
    assert _pending(tmp_path) == []


def test_a_dry_run_that_crashes_changes_no_row_either(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same invariant on the unexpected-exception path: the crash is re-raised
    for the worker's run record, but it marks nothing."""
    monkeypatch.setattr(compose, "load_candidate_pool", _boom)

    with pytest.raises(RuntimeError):
        run_compose(world, dry_run=True)

    assert world["failed"] == []
    assert world["written"] == []


# ── the package facade stays cheap ──────────────────────────────────────────


def test_importing_the_package_does_not_pull_the_writer_stack() -> None:
    """`api/` imports `spotlight.post_products` and `spotlight.prompts`, which
    run this package's `__init__` first. An eager `from .compose import ...`
    there would put crewai and the image client behind every FastAPI start-up:
    a failure in that stack would take the API down instead of failing one
    flow. The facade resolves the name lazily (PEP 562) instead."""
    import lib.crew.spotlight as package

    tree = ast.parse(Path(package.__file__ or "").read_text(encoding="utf-8"))
    eager = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0
    }
    assert "lib.crew.spotlight.compose" not in eager
    assert "lib.crew.spotlight.inputs" not in eager
    # ...and the name still resolves, so every documented spelling works.
    assert package.compose_spotlight is compose.compose_spotlight
    assert "compose_spotlight" in package.__all__
    with pytest.raises(AttributeError):
        _ = package.no_such_name
