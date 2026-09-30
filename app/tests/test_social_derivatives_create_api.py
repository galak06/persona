"""Tests for the product-spotlight creation routes: sources, picker, create.

Same convention as `test_social_posts_retry_api.py`: every collaborator that
crosses a process boundary is faked and RECORDED rather than mocked away -- the
Redis `flow-run` queue, the shared `worker_runs` row, the `content_derivatives`
table, the ideas table, the live WordPress post and the product catalog. No
Postgres, no Redis, no WordPress, no run.

`build_world` lives here rather than in a `_fakes` module because
`tests/test_social_derivatives_api.py` is its only other caller and the two
files together tell one story: this one covers the half that CREATES a
spotlight, that one the half that reviews it, and the end-to-end test over
there starts from the world built here so the two halves cannot drift.

The load-bearing assertions are the three negative ones: this route dispatches
a script with no publisher in it, a product the article does NOT link is
allowed (the flag is the compose step's job, not a gate here), and WordPress
being unreachable degrades the picker instead of breaking it -- which is the
normal state of the API container, whose environment carries no WP credentials.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from api import social_derivatives_api as review_api
from api import social_derivatives_create_api as create_api
from api import social_derivatives_support as support
from fastapi import HTTPException

from lib import derivatives_db, flow_queue
from lib.affiliate_resolver import ProductEntry
from lib.errors import ConfigurationError
from tests._reference_library_fakes import write_library as _write_library

BRAND = "b1"
IDEA_ID = "idea-1"
FOCUS = "Dental Care"

#: The picker's catalog. Two of these are linked by the article, one shares the
#: post's category, and one is off-category -- which is exactly the shape the
#: `scope=category` filter has to get right.
CHEW_ASIN = "B00DENTAL1"
ROPE_ASIN = "B00ROPEY01"
OIL_ASIN = "B00SALMON1"
BISON_ASIN = "B00BISON01"
#: Linked by the post, carried by no catalog entry.
LOST_ASIN = "B00UNKNOW1"

POST_HTML = (
    f'<p>intro</p><a href="https://www.amazon.com/dp/{CHEW_ASIN}/ref=x">chew</a>'
    f'<a href="https://amzn.to/dp/{ROPE_ASIN}">rope</a>'
    f'<a href="https://www.amazon.com/gp/product/{LOST_ASIN}">gone</a>'
)


def catalog() -> dict[str, ProductEntry]:
    """`load_candidate_pool`'s answer: curated entries first, as it returns."""
    return {
        # Deliberately capital-Z display next to a lowercase-b one: a naive
        # `sorted(key=display)` puts "Zesty" before "bison", which is the bug
        # the case-insensitive sort exists to avoid.
        "salmon-oil": ProductEntry(
            key="salmon-oil",
            asin=OIL_ASIN,
            display="Zesty Salmon Oil",
            category="Dental Care",
            notes="verified against the VOHC list",
        ),
        "bison-treat": ProductEntry(
            key="bison-treat", asin=BISON_ASIN, display="bison Crunch", category="Toys"
        ),
        "dental-chew": ProductEntry(
            key="dental-chew", asin=CHEW_ASIN, display="Alpha Dental Chew", category="dental-care"
        ),
        "toy-rope": ProductEntry(
            key="toy-rope", asin=ROPE_ASIN, display="Rope Toy", category="Toys"
        ),
    }


def published_idea(**overrides: Any) -> dict[str, Any]:
    idea = {
        "id": IDEA_ID,
        "brand_id": BRAND,
        "topic": "Best dental chews",
        "category": FOCUS,
        "status": "wp_published",
        "wp_post_id": "4463",
        "wp_url": "https://dogfoodandfun.com/best-dental-chews/",
    }
    idea.update(overrides)
    return idea


class FakeDerivativesDb:
    """`content_derivatives` in a dict, with the table's real guards.

    Only the guards the routes DEPEND on are modelled: the partial unique index
    (one active row per idea+product+format), the `'queued'`-only transitions,
    and the `None` return that means "duplicate OR write failure". Everything
    else is a plain dict, because a fake that reimplements SQL is a second
    implementation to keep in sync.
    """

    STATUSES = (
        "composing",
        "queued",
        "scheduled",
        "fb_publishing",
        "fb_published",
        "ig_publishing",
        "published",
        "rejected",
        "failed",
    )
    ACTIVE = ("composing", "queued", "scheduled")

    def __init__(self, events: list[str]) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.events = events
        self.swept: list[str] = []
        self.insert_ok = True
        self.last_slot: Any = None
        self._seq = 0

    # -- reads ---------------------------------------------------------------
    def get(self, derivative_id: str) -> dict[str, Any] | None:
        return self.rows.get(derivative_id)

    def find_active(self, idea_id: str, product_key: str, fmt: str) -> dict[str, Any] | None:
        for row in self.rows.values():
            if (row["idea_id"], row["product_key"], row["format"]) == (
                idea_id,
                product_key,
                fmt,
            ) and row["status"] in self.ACTIVE:
                return row
        return None

    def list_for_review(
        self, *, brand_id: str | None = None, status: str = "queued", limit: int = 100
    ) -> list[dict[str, Any]]:
        widened = {status, "composing", "fb_publishing", "ig_publishing"}
        wanted = widened if status == "queued" else {status}
        rows = [
            r
            for r in self.rows.values()
            if r["status"] in wanted and (brand_id is None or r["brand_id"] == brand_id)
        ]
        return rows[:limit]

    def last_scheduled_fb_slot(self, *, brand_id: str | None = None) -> Any:
        return self.last_slot

    # -- writes --------------------------------------------------------------
    def fail_stale_composing(self, *, brand_id: str, older_than_seconds: int = 1200) -> int:
        self.swept.append(brand_id)
        return 0

    def insert_composing(self, new: Any) -> str | None:
        self.events.append("insert")
        if not self.insert_ok:
            return None
        if self.find_active(new.idea_id, new.product_key, new.format) is not None:
            return None
        self._seq += 1
        new_id = f"deriv-{self._seq}"
        self.rows[new_id] = {"id": new_id, "status": "composing", **asdict(new)}
        return new_id

    def mark_failed(self, derivative_id: str, *, error: str) -> bool:
        row = self.rows.get(derivative_id)
        if row is None or row["status"] != "composing":
            return False
        row.update(status="failed", error=error)
        self.events.append(f"mark_failed:{error}")
        return True

    def schedule_fb(self, derivative_id: str, *, due_at: Any) -> bool:
        row = self.rows.get(derivative_id)
        if row is None or row["status"] != "queued":
            return False
        row.update(status="scheduled", fb_due_at=due_at)
        return True

    def unschedule_fb(self, derivative_id: str) -> bool:
        row = self.rows.get(derivative_id)
        if row is None or row["status"] != "scheduled":
            return False
        row.update(status="queued", fb_due_at=None)
        return True

    def reject(self, derivative_id: str) -> bool:
        row = self.rows.get(derivative_id)
        if row is None or row["status"] != "queued":
            return False
        row["status"] = "rejected"
        return True


def build_world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Every collaborator outside the two route modules, recorded in `state`."""
    events: list[str] = []
    fake_db = FakeDerivativesDb(events)
    state: dict[str, Any] = {
        "brand_dir": tmp_path,
        "db": fake_db,
        "events": events,
        "ideas": [published_idea()],
        "post": {"slug": "best-dental-chews", "content": {"rendered": POST_HTML}},
        "wp_error": None,
        "pool": catalog(),
        "pushed": [],
        "queued": [],
        "worker_row": None,
        "push_fails": False,
        "regular_slot": None,
    }
    _write_library(tmp_path, {"kitchen-counter": "counter-bytes", "studio-mascot": "mascot-bytes"})

    monkeypatch.setattr("api.brand_context.brands_db.get", lambda _b: {"brand_dir": str(tmp_path)})
    monkeypatch.setattr("api.brand_context.current_brand_id", lambda: BRAND)
    monkeypatch.setattr(support.brands_db, "focus_category", lambda _b: FOCUS)

    # The one table the whole feature turns on, swapped on BOTH modules that
    # reach it (the routes and the helpers they share).
    for module in (create_api, review_api, support):
        monkeypatch.setattr(module, "derivatives_db", fake_db)

    def _get_idea(idea_id: str) -> dict[str, Any] | None:
        return next((i for i in state["ideas"] if i["id"] == idea_id), None)

    def _list_ideas(*, status: str | None = None, brand_id: str | None = None, limit: int = 500):
        if state.get("ideas_raise"):
            raise RuntimeError("no DATABASE_URL")
        return [i for i in state["ideas"] if status is None or i["status"] == status][:limit]

    monkeypatch.setattr(support.ideas_db, "get_idea", _get_idea)
    monkeypatch.setattr(support.ideas_db, "list_ideas", _list_ideas)

    def _fetch_post(_wp_post_id: str) -> dict[str, Any] | None:
        if state["wp_error"] is not None:
            raise state["wp_error"]
        return state["post"]

    monkeypatch.setattr(support.wp_source, "fetch_post", _fetch_post)
    monkeypatch.setattr(create_api, "load_candidate_pool", lambda _dir: state["pool"])
    monkeypatch.setattr(create_api.worker_db, "get_one", lambda _d, _l, _b: state["worker_row"])
    monkeypatch.setattr(
        create_api.worker_db,
        "record_queued",
        lambda _d, label, brand: state["queued"].append((label, brand)),
    )

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            state["queue"] = (worker, brand)

        def push(self, payload: dict[str, Any]) -> str:
            if state["push_fails"]:
                raise ConnectionError("redis is down")
            state["events"].append("push")
            state["pushed"].append(payload)
            return "1"

    monkeypatch.setattr(flow_queue, "TaskQueue", _FakeQueue)

    # The REAL shared-calendar allocator over faked readers, so "a spotlight
    # lands after a regular post's slot" is the arithmetic, not an assumption.
    monkeypatch.setattr(review_api.social_slot_allocator, "derivatives_db", fake_db)
    monkeypatch.setattr(
        review_api.social_slot_allocator.social_post_db,
        "last_scheduled_fb_slot",
        lambda *, brand_id=None: state["regular_slot"],
    )
    return state


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return build_world(monkeypatch, tmp_path)


def _create(**overrides: Any) -> Any:
    body = {"idea_id": IDEA_ID, "product_key": "dental-chew"}
    body.update(overrides)
    return create_api.create_spotlight(create_api.CreateSpotlightRequest(**body))


# ── sources ─────────────────────────────────────────────────────────────────


def test_published_in_focus_posts_are_offered(world: dict[str, Any]) -> None:
    """An idea whose own social post already shipped is STILL a source -- a
    second, product-led post out of an earning article is the whole feature."""
    world["ideas"].append(published_idea(id="idea-2", status="social_done", topic="Chew sizing"))

    posts = create_api.list_spotlight_sources().posts

    assert [p.idea_id for p in posts] == [IDEA_ID, "idea-2"]
    assert posts[0].wp_url.endswith("/best-dental-chews/")


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"id": "x1", "category": "Toys"}, "out of focus"),
        ({"id": "x2", "wp_post_id": None}, "never published"),
        ({"id": "x3", "wp_url": ""}, "no permalink"),
    ],
)
def test_a_post_without_a_live_article_is_not_a_source(
    world: dict[str, Any], overrides: dict[str, Any], why: str
) -> None:
    world["ideas"] = [published_idea(**overrides)]

    assert create_api.list_spotlight_sources().posts == [], why


def test_a_dead_database_is_reported_not_rendered_as_an_empty_picker(
    world: dict[str, Any],
) -> None:
    """`list_ideas` raises on purpose; an empty list here would read as "this
    brand has published nothing"."""
    world["ideas_raise"] = True

    with pytest.raises(HTTPException) as exc:
        create_api.list_spotlight_sources()

    assert exc.value.status_code == 500


# ── product picker ──────────────────────────────────────────────────────────


def test_the_picker_puts_the_articles_own_products_first(world: dict[str, Any]) -> None:
    resp = create_api.list_spotlight_products(IDEA_ID)

    # Post link order for the two in-post products, then the catalog by display
    # name. "Zesty Salmon Oil" sorts after nothing here because the only other
    # candidate is off-category and `scope=category` dropped it.
    assert [p.key for p in resp.products] == ["dental-chew", "toy-rope", "salmon-oil"]
    assert [p.in_post for p in resp.products] == [True, True, False]
    assert resp.post_scan == "ok"
    assert resp.slug == "best-dental-chews"
    assert resp.category == "dental-care"
    assert resp.unknown_asins == [LOST_ASIN]
    # The verified note is what licenses a certification claim downstream.
    assert [p.key for p in resp.products if p.certification_verified] == ["salmon-oil"]


def test_an_in_post_product_survives_the_category_filter(world: dict[str, Any]) -> None:
    """`toy-rope` is filed under Toys, not the post's category. The article
    links it, so it is relevant to the article whatever the catalog says."""
    resp = create_api.list_spotlight_products(IDEA_ID, scope="category")

    rope = next(p for p in resp.products if p.key == "toy-rope")
    assert (rope.in_post, rope.category) == (True, "Toys")
    assert "bison-treat" not in {p.key for p in resp.products}


def test_show_all_categories_opens_the_whole_catalog(world: dict[str, Any]) -> None:
    resp = create_api.list_spotlight_products(IDEA_ID, scope="all")

    # Capital "Zesty" after lowercase "bison": the sort is case-insensitive.
    assert [p.key for p in resp.products] == [
        "dental-chew",
        "toy-rope",
        "bison-treat",
        "salmon-oil",
    ]
    assert resp.scope == "all"


@pytest.mark.parametrize(
    "failure",
    [None, ConfigurationError("WP_URL is not set"), ValueError("<html>not json</html>")],
    ids=["post-not-retrievable", "no-wp-credentials", "non-json-body"],
)
def test_an_unreachable_wordpress_degrades_the_picker_instead_of_breaking_it(
    world: dict[str, Any], failure: Exception | None
) -> None:
    """THE CONTAINER PROPERTY. `persona-api-1` carries no WP credentials at
    all, so "WordPress did not answer" is the normal case, not an error."""
    if failure is None:
        world["post"] = None
    else:
        world["wp_error"] = failure

    resp = create_api.list_spotlight_products(IDEA_ID, scope="all")

    assert resp.post_scan == "unavailable"
    assert not any(p.in_post for p in resp.products)
    assert resp.unknown_asins == []
    # Slug still resolves -- off the permalink rather than the post record.
    assert resp.slug == "best-dental-chews"
    assert len(resp.products) == 4


def test_an_http_failure_also_degrades(world: dict[str, Any]) -> None:
    world["wp_error"] = support.httpx.ConnectError("connection refused")

    assert create_api.list_spotlight_products(IDEA_ID).post_scan == "unavailable"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ({"ideas": []}, 404),
        ({"ideas": [published_idea(wp_post_id=None)]}, 409),
        ({"ideas": [published_idea(category="Toys")]}, 422),
    ],
    ids=["unknown-idea", "not-published", "out-of-focus"],
)
def test_the_picker_refuses_an_unusable_post(
    world: dict[str, Any], setup: dict[str, Any], code: int
) -> None:
    world.update(setup)

    with pytest.raises(HTTPException) as exc:
        create_api.list_spotlight_products(IDEA_ID)

    assert exc.value.status_code == code


def test_another_brands_idea_is_a_404(world: dict[str, Any]) -> None:
    world["ideas"] = [published_idea(brand_id="someone-else")]

    with pytest.raises(HTTPException) as exc:
        create_api.list_spotlight_products(IDEA_ID)

    assert exc.value.status_code == 404


# ── create + dispatch ───────────────────────────────────────────────────────


def test_the_compose_run_is_dispatched_to_the_worker(world: dict[str, Any]) -> None:
    """The API image has no LLM/image credentials and no fonts, so the run goes
    on the shared queue rather than executing here."""
    resp = _create(reference_category="Kitchen Counter")

    assert (resp.id, resp.status) == ("deriv-1", "composing")
    assert world["queue"] == ("flow-run", BRAND)
    payload = world["pushed"][0]
    assert payload["script"] == "scripts/social_derivative_compose.py"
    assert payload["args"] == ["--derivative-id", "deriv-1"]
    assert payload["brand"] == BRAND
    assert payload["timeout_seconds"] == 900
    assert world["queued"] == [(f"{BRAND}-social-derivative-compose", BRAND)]
    # The row exists BEFORE the push, because the id IS the script's argument.
    assert world["events"] == ["insert", "push"]
    row = world["db"].rows["deriv-1"]
    assert (row["product_asin"], row["reference_category"]) == (CHEW_ASIN, "kitchen-counter")


def test_this_route_cannot_publish(world: dict[str, Any]) -> None:
    """THE SAFETY PROPERTY. The script it names imports no publisher, no worker
    and no release sweep, so no argument shape can reach a platform."""
    _create()

    payload = world["pushed"][0]
    assert payload["script"] != "scripts/crewai_social_posts_pipeline.py"
    assert not any(a.startswith("--release") for a in payload["args"])
    script = Path(__file__).resolve().parents[1] / payload["script"]
    if not script.exists():  # phase 2H owns it; the arg assertions still hold
        pytest.skip("scripts/social_derivative_compose.py not written yet")
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    assert not any("publish" in n or "worker" in n or "release" in n for n in imported)


def test_a_product_the_article_never_linked_is_allowed(world: dict[str, Any]) -> None:
    """Deliberate: a spotlight on a catalog product the post does not link is a
    legitimate post. Compose FLAGS it (`product_not_in_post`); it is not a
    gate, or the feature would only ever repeat what the article already said."""
    resp = _create(product_key="bison-treat")

    assert resp.status == "composing"
    assert world["db"].rows[resp.id]["product_asin"] == BISON_ASIN


def test_a_duplicate_carries_the_existing_spotlights_id(world: dict[str, Any]) -> None:
    first = _create()

    with pytest.raises(HTTPException) as exc:
        _create()

    assert exc.value.status_code == 409
    assert exc.value.detail["existing_id"] == first.id
    assert len(world["pushed"]) == 1


def test_a_write_failure_is_a_503_not_a_phantom_duplicate(world: dict[str, Any]) -> None:
    """`insert_composing` answers None for both cases; no active row means the
    write itself failed."""
    world["db"].insert_ok = False

    with pytest.raises(HTTPException) as exc:
        _create()

    assert exc.value.status_code == 503
    assert world["pushed"] == []


def test_an_unknown_product_key_is_refused(world: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        _create(product_key="not-in-catalog")

    assert exc.value.status_code == 422
    assert world["db"].rows == {}


def test_a_collection_with_no_photos_is_refused_rather_than_ignored(
    world: dict[str, Any],
) -> None:
    with pytest.raises(HTTPException) as exc:
        _create(reference_category="products")

    assert exc.value.status_code == 422
    assert world["db"].rows == {}


def test_an_out_of_focus_post_cannot_be_spotlighted(world: dict[str, Any]) -> None:
    world["ideas"] = [published_idea(category="Toys")]

    with pytest.raises(HTTPException) as exc:
        _create()

    assert exc.value.status_code == 422
    assert world["pushed"] == []


def test_a_second_compose_while_one_runs_is_refused(world: dict[str, Any]) -> None:
    """One worker slot per brand; a queued run means the next click would just
    stack behind it for 15 minutes."""
    first = _create()
    world["worker_row"] = {"status": "running"}
    assert world["db"].rows[first.id]["status"] == "composing"

    with pytest.raises(HTTPException) as exc:
        _create(product_key="bison-treat")

    assert exc.value.status_code == 409
    assert len(world["pushed"]) == 1


def test_a_worker_row_the_container_outlived_no_longer_blocks_creates(
    world: dict[str, Any],
) -> None:
    """`record_complete` never fires when the container is recreated mid-run, so
    a 'running' `worker_runs` row outlives its process and nothing ever clears
    it -- on its own it would answer 409 to every later create FOREVER, with no
    affordance in the UI. The composing ROW is the half that expires (step 2's
    sweep), so the guard needs both."""
    world["worker_row"] = {"status": "running"}  # stuck, and will never complete

    resp = _create()  # no 'composing' row survived the sweep

    assert resp.status == "composing"
    assert len(world["pushed"]) == 1


def test_a_stale_compose_is_swept_before_anything_else(world: dict[str, Any]) -> None:
    """A run the worker was killed under would otherwise hold the unique index
    and answer 409 to its own re-creation for the full stale window."""
    _create()

    assert world["db"].swept == [BRAND]


def test_a_failed_dispatch_fails_the_row_it_would_have_composed(
    world: dict[str, Any],
) -> None:
    """The row is created before the push, so a dead queue must not leave a
    'composing' row nothing will ever pick up."""
    world["push_fails"] = True

    with pytest.raises(HTTPException) as exc:
        _create()

    assert exc.value.status_code == 503
    assert world["events"] == ["insert", "mark_failed:dispatch_failed"]
    assert world["db"].rows["deriv-1"]["status"] == "failed"
    assert world["queued"] == []


def test_the_compose_timeout_stays_below_the_stale_sweep() -> None:
    """A run must be killed by ITS timeout before the stale sweep may fail its
    row from under it, or a live run's write lands on a 'failed' row."""
    assert create_api._COMPOSE_TIMEOUT_SECONDS < derivatives_db.STALE_COMPOSING_SECONDS
