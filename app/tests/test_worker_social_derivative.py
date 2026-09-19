"""Tests for the product-spotlight publish worker
(`recipe-publisher/workers/worker_social_derivative.py`).

What these pin is the ORDER of the guard chain and, above all, what happens
around a live post: this worker is the only code path that can put a spotlight
on the brand's Facebook Page and Instagram account, and every bug class it can
have is expensive in exactly one direction -- a double post cannot be deleted
from people's feeds, while a row stuck mid-flight costs one manual UPDATE. So:

* a publisher that RAISED must give the claim back (the row retries),
* a result write that FAILED after a live post must NOT (the row wedges),
* `--dry-run` must not reach a publisher, an uploader or even the claim.

The worker lives under `recipe-publisher/`, whose hyphen makes it
un-importable as a package (it runs as `python -m workers.worker_social_
derivative` with cwd set into that directory), so it is loaded here by file
path -- precedent `test_wp_ideas_drafter_lease.py`. `publishers.facebook` /
`publishers.instagram` are replaced in `sys.modules` BEFORE the module is
executed: importing the real ones pulls in the Graph API clients, and the
whole point of this file is that nothing here can post.
"""
# ruff: noqa: S101

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lib.engagements_db.models import dedup_id

_WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "recipe-publisher"
    / "workers"
    / "worker_social_derivative.py"
)

_DID = "deriv-1"
_IDEA_ID = "idea-9"


# ─────────────────────────────────────────────────────────────────────────────
# Fakes. Every seam the worker can reach the outside world through.


@dataclass
class _FBResult:
    post_id: str = "fb-post-1"
    permalink: str | None = "https://facebook.com/p/1"


@dataclass
class _IGResult:
    media_id: str = "ig-media-1"
    permalink: str | None = "https://instagram.com/p/1"


@dataclass
class _FakeDerivativesDb:
    """In-memory stand-in for `lib.derivatives_db` (phase 1A owns the real
    bodies, which still raise `NotImplementedError` while waves run in
    parallel)."""

    row: dict[str, Any] | None
    claim_ok: bool = True
    write_ok: bool = True
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def _note(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    @property
    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def kwargs_of(self, name: str) -> dict[str, Any]:
        return next(c[2] for c in self.calls if c[0] == name)

    def get(self, derivative_id: str) -> dict[str, Any] | None:
        self._note("get", derivative_id)
        return self.row

    def claim_publish(self, derivative_id: str, platform: str) -> bool:
        self._note("claim_publish", derivative_id, platform)
        return self.claim_ok

    def release_publish_claim(self, derivative_id: str, platform: str) -> bool:
        self._note("release_publish_claim", derivative_id, platform)
        return True

    def set_fb_result(self, derivative_id: str, *, url: str, ig_gap_hours: float) -> bool:
        self._note("set_fb_result", derivative_id, url=url, ig_gap_hours=ig_gap_hours)
        return self.write_ok

    def set_ig_result(self, derivative_id: str, *, url: str) -> bool:
        self._note("set_ig_result", derivative_id, url=url)
        return self.write_ok


def _load_worker(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _no_publish(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a test must patch the publisher before it can be called")

    package = types.ModuleType("publishers")
    package.__path__ = []  # type: ignore[attr-defined]
    facebook = types.ModuleType("publishers.facebook")
    facebook.publish_photo_post_to_facebook = _no_publish  # type: ignore[attr-defined]
    instagram = types.ModuleType("publishers.instagram")
    instagram.publish_to_instagram = _no_publish  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "publishers", package)
    monkeypatch.setitem(sys.modules, "publishers.facebook", facebook)
    monkeypatch.setitem(sys.modules, "publishers.instagram", instagram)

    spec = importlib.util.spec_from_file_location(
        "_worker_social_derivative_under_test", _WORKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    mod = _load_worker(monkeypatch)
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setattr(mod.rate_limiter, "can_act", lambda *_a: True)
    monkeypatch.setattr(mod.rate_limiter, "record_action", lambda *_a: 1)
    monkeypatch.setattr(mod, "record_publish", lambda **_k: "eng-1")
    return mod


def _row(**overrides: Any) -> dict[str, Any]:
    past = datetime.now(UTC) - timedelta(minutes=5)
    row: dict[str, Any] = {
        "id": _DID,
        "idea_id": _IDEA_ID,
        "format": "feed_post",
        "status": "scheduled",
        "fb_due_at": past,
        "ig_due_at": past,
        "fb_caption": "fb words",
        "ig_caption": "ig words",
        "image_path": "state/social_posts_pending/spotlight-deriv-1.jpg",
        "image_alt": "a dog",
        "source": "gemini",
    }
    row.update(overrides)
    return row


def _install(worker: Any, monkeypatch: pytest.MonkeyPatch, db: _FakeDerivativesDb) -> None:
    monkeypatch.setattr(worker, "derivatives_db", db)


def _write_image(tmp_path: Path, row: dict[str, Any]) -> Path:
    path = tmp_path / str(row["image_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpeg-bytes")
    return path


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        json.loads(r.message)["event"] for r in caplog.records if r.message.startswith('{"event"')
    ]


def _recorded(monkeypatch: pytest.MonkeyPatch, worker: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(worker, "record_publish", lambda **kw: seen.append(kw) or "eng-1")
    return seen


def _rate_actions(monkeypatch: pytest.MonkeyPatch, worker: Any) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(worker.rate_limiter, "record_action", lambda p, a: seen.append((p, a)) or 1)
    return seen


def _fake_ig_upload(worker: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake the WP media upload at its three import seams, so the media NAME
    (`social-derivative-<id>`) is still asserted rather than stubbed away."""
    names: list[str] = []

    @dataclass
    class _GeneratedImage:
        url: str
        alt_text: str
        provider: str
        bytes_: bytes
        content_type: str

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    def _upload(_client: Any, _image: Any, slug: str) -> tuple[int, str]:
        names.append(slug)
        return 7, "https://wp.example/media/7.jpg"

    wp_image = types.ModuleType("lib.crew.wp_image")
    wp_image.GeneratedImage = _GeneratedImage  # type: ignore[attr-defined]
    wp_media = types.ModuleType("lib.crew.wp_media")
    wp_media.upload_wp_media = _upload  # type: ignore[attr-defined]
    wp_client = types.ModuleType("lib.sessions.wp_client")
    wp_client.wp_client = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.crew.wp_image", wp_image)
    monkeypatch.setitem(sys.modules, "lib.crew.wp_media", wp_media)
    monkeypatch.setitem(sys.modules, "lib.sessions.wp_client", wp_client)
    return names


# ─────────────────────────────────────────────────────────────────────────────
# Guards -- everything that must stop BEFORE a claim is taken


def test_a_row_in_the_wrong_status_is_skipped_without_claiming(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _FakeDerivativesDb(_row(status="queued"))
    _install(worker, monkeypatch, db)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "skipped"

    assert "claim_publish" not in db.names
    assert "social_derivative_publish_skipped_wrong_status" in _events(caplog)


def test_the_ig_half_refuses_to_run_before_the_fb_half(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'scheduled' is the FB half's status: publishing IG from it would put the
    IG post out first and skip the documented gap entirely."""
    db = _FakeDerivativesDb(_row(status="scheduled"))
    _install(worker, monkeypatch, db)

    assert worker._do_one(_DID, "ig", dry_run=False) == "skipped"
    assert "claim_publish" not in db.names


def test_a_row_whose_slot_has_not_arrived_is_skipped(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _FakeDerivativesDb(_row(fb_due_at=datetime.now(UTC) + timedelta(hours=3)))
    _install(worker, monkeypatch, db)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "skipped"

    assert "claim_publish" not in db.names
    assert "social_derivative_publish_skipped_not_due" in _events(caplog)


def test_a_missing_row_is_an_error(worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(worker, monkeypatch, _FakeDerivativesDb(None))

    assert worker._do_one(_DID, "fb", dry_run=False) == "error"


def test_a_format_without_a_publisher_is_refused_not_guessed(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _FakeDerivativesDb(_row(format="carousel"))
    _install(worker, monkeypatch, db)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "unsupported_format"

    assert "claim_publish" not in db.names
    assert "social_derivative_publish_unsupported_format" in _events(caplog)


def test_an_exhausted_rate_limit_stops_the_publish(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = _FakeDerivativesDb(_row())
    _install(worker, monkeypatch, db)
    monkeypatch.setattr(worker.rate_limiter, "can_act", lambda *_a: False)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "rate_limited"

    assert "claim_publish" not in db.names
    assert "social_derivative_publish_rate_limited" in _events(caplog)


def test_a_lost_claim_ends_the_run_quietly(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The other holder is publishing this exact row: exit 0, no release (that
    would yank the claim out from under the process that IS posting)."""
    db = _FakeDerivativesDb(_row(), claim_ok=False)
    _install(worker, monkeypatch, db)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "claim_lost"

    assert "release_publish_claim" not in db.names
    assert "social_derivative_publish_claim_lost" in _events(caplog)
    assert worker.main(["--derivative-id", _DID, "--platform", "fb"]) == 0


def test_dry_run_touches_nothing_at_all(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No publisher, no uploader, and no claim: a dry run must leave the row
    exactly as due as it found it."""
    db = _FakeDerivativesDb(_row())
    _install(worker, monkeypatch, db)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=True) == "dry_run"

    assert db.names == ["get"]
    assert "social_derivative_publish_dry_run" in _events(caplog)


# ─────────────────────────────────────────────────────────────────────────────
# The publish itself


def test_fb_happy_path_records_the_live_post_everywhere(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    row = _row()
    image = _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    sent: dict[str, Any] = {}
    monkeypatch.setattr(
        worker,
        "publish_photo_post_to_facebook",
        lambda **kw: sent.update(kw) or _FBResult(),
    )
    published = _recorded(monkeypatch, worker)
    actions = _rate_actions(monkeypatch, worker)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "published"

    assert sent["image_path"] == image
    assert sent["message"] == "fb words"
    assert sent["alt_text"] == "a dog"
    assert db.names == ["get", "claim_publish", "set_fb_result"]
    assert db.kwargs_of("set_fb_result") == {
        "url": "https://facebook.com/p/1",
        "ig_gap_hours": worker._IG_GAP_HOURS,
    }
    assert actions == [("facebook", "page_post")]
    assert published[0]["platform"] == "facebook"
    assert published[0]["kind"] == "page_post"
    assert published[0]["permalink"] == "https://facebook.com/p/1"
    assert published[0]["content"] == "fb words"
    assert published[0]["source_ref"] == f"derivative:{_DID}"
    assert published[0]["ref"] == f"derivative:{_DID}"
    assert "social_derivative_published_fb" in _events(caplog)
    # The FB half must NOT delete the image -- the IG half still needs it.
    assert image.exists()
    timeline = json.loads((tmp_path / "state" / "publishing_timeline.json").read_text())
    assert "last_fb_page_post" in timeline


def test_ig_happy_path_uploads_publishes_and_then_drops_the_image(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    row = _row(status="fb_published")
    image = _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    names = _fake_ig_upload(worker, monkeypatch)
    sent: dict[str, Any] = {}

    def _publish(stub: Any, *, image_url: str) -> _IGResult:
        sent["slug"] = stub.slug
        sent["caption"] = stub.ig_caption
        sent["image_url"] = image_url
        return _IGResult()

    monkeypatch.setattr(worker, "publish_to_instagram", _publish)
    published = _recorded(monkeypatch, worker)
    actions = _rate_actions(monkeypatch, worker)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "ig", dry_run=False) == "published"

    assert names == [f"social-derivative-{_DID}"]
    assert sent == {
        "slug": f"spotlight-{_DID}",
        "caption": "ig words",
        "image_url": "https://wp.example/media/7.jpg",
    }
    assert db.kwargs_of("set_ig_result") == {"url": "https://instagram.com/p/1"}
    assert actions == [("instagram", "feed_post")]
    assert published[0]["kind"] == "feed_post"
    assert published[0]["source_ref"] == f"derivative:{_DID}"
    assert published[0]["ref"] == f"derivative:{_DID}"
    assert "social_derivative_published_ig" in _events(caplog)
    assert not image.exists()


def test_two_spotlights_from_one_post_get_two_engagement_rows(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ref` is the engagements PRIMARY KEY (`dedup_id(platform, kind, ref)`, an
    upsert). This feature exists to make MANY derivatives per idea, so a `ref`
    of the idea id would guarantee a collision: the second FB spotlight for the
    same post would silently overwrite the first and `/published` would
    under-count. Proven against the real key function, not a string shape."""
    published = _recorded(monkeypatch, worker)
    monkeypatch.setattr(worker, "publish_photo_post_to_facebook", lambda **_kw: _FBResult())
    rows = [_row(id="deriv-1"), _row(id="deriv-2")]
    assert len({r["idea_id"] for r in rows}) == 1  # the premise: one source post

    for row in rows:
        _write_image(tmp_path, row)
        _install(worker, monkeypatch, _FakeDerivativesDb(row))
        assert worker._do_one(str(row["id"]), "fb", dry_run=False) == "published"

    assert len(published) == 2
    assert len({dedup_id(r["platform"], r["kind"], r["ref"]) for r in published}) == 2
    assert {r["source_ref"] for r in published} == {"derivative:deriv-1", "derivative:deriv-2"}


def test_a_publisher_that_raises_gives_the_claim_back(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing went out, so the row must become due again -- not wedge."""
    row = _row()
    _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    published = _recorded(monkeypatch, worker)

    def _boom(**_kw: Any) -> Any:
        raise RuntimeError("graph api 500")

    monkeypatch.setattr(worker, "publish_photo_post_to_facebook", _boom)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "error"

    assert db.names == ["get", "claim_publish", "release_publish_claim"]
    assert published == []
    assert "social_derivative_publish_failed" in _events(caplog)
    assert worker.main(["--derivative-id", _DID, "--platform", "fb"]) == 1


def test_a_publisher_that_returns_no_id_also_gives_the_claim_back(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = _row()
    _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    monkeypatch.setattr(
        worker, "publish_photo_post_to_facebook", lambda **_kw: _FBResult(post_id="")
    )

    assert worker._do_one(_DID, "fb", dry_run=False) == "error"
    assert "release_publish_claim" in db.names
    assert "set_fb_result" not in db.names


def test_a_failed_result_write_wedges_the_row_but_still_counts_the_post(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The post IS live. Releasing the claim here would re-publish it on the
    next sweep, so the row is left in 'fb_publishing' (which nothing selects)
    and the rate limiter + engagements log still see the post."""
    row = _row()
    _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row, write_ok=False)
    _install(worker, monkeypatch, db)
    monkeypatch.setattr(worker, "publish_photo_post_to_facebook", lambda **_kw: _FBResult())
    published = _recorded(monkeypatch, worker)
    actions = _rate_actions(monkeypatch, worker)

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "fb", dry_run=False) == "wedged"

    assert "release_publish_claim" not in db.names
    assert actions == [("facebook", "page_post")]
    assert published[0]["permalink"] == "https://facebook.com/p/1"
    assert "social_derivative_result_write_failed" in _events(caplog)
    assert worker.main(["--derivative-id", _DID, "--platform", "fb"]) == 1


def test_bookkeeping_that_blows_up_never_releases_a_live_post(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`rate_limiter.record_action` raises once the daily cap is hit. That must
    not reach the failure handler: the post already exists."""
    row = _row()
    _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    monkeypatch.setattr(worker, "publish_photo_post_to_facebook", lambda **_kw: _FBResult())
    monkeypatch.setattr(
        worker.rate_limiter,
        "record_action",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("cap exceeded")),
    )

    assert worker._do_one(_DID, "fb", dry_run=False) == "published"
    assert "release_publish_claim" not in db.names


def _live_ig_post(worker: Any, monkeypatch: pytest.MonkeyPatch, *, unlink_error: OSError) -> None:
    """Arm the IG half so the publisher and the result write both succeed and
    only the image cleanup fails -- a read-only bind mount or a path swapped for
    a directory, both of which surface as `OSError`."""
    _fake_ig_upload(worker, monkeypatch)
    monkeypatch.setattr(worker, "publish_to_instagram", lambda *_a, **_k: _IGResult())
    monkeypatch.setattr(Path, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(unlink_error))


def test_a_cleanup_failure_after_a_live_ig_post_never_releases_the_claim(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Deleting the local image is the LAST thing the IG half does, and it is
    bookkeeping: the post is already on the account. Letting that raise reach
    `_do_one`'s handler would release the claim -- 'ig_publishing' ->
    'fb_published' with `ig_due_at` still in the past -- and the next hourly
    sweep would publish the same Instagram post a second time (and re-upload
    the WP media). An undeleted file is the cheaper failure by far."""
    row = _row(status="fb_published")
    image = _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row)
    _install(worker, monkeypatch, db)
    _live_ig_post(worker, monkeypatch, unlink_error=PermissionError("read-only file system"))

    with caplog.at_level(logging.INFO):
        assert worker._do_one(_DID, "ig", dry_run=False) == "published"

    assert "release_publish_claim" not in db.names
    assert "set_ig_result" in db.names
    assert "social_derivative_publish_failed" not in _events(caplog)
    assert "social_derivative_published_ig" in _events(caplog)
    assert image.exists()  # the stray file is the entire cost


def test_a_cleanup_failure_on_a_wedged_ig_write_also_keeps_the_claim(
    worker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both post-publish failures stacked: the IG post is live, `set_ig_result`
    returned False (the intended `wedged` row, which nothing selects), and the
    unlink raises on top. Still no release -- otherwise the row goes straight
    back to the sweep, due, with a live post already on the account."""
    row = _row(status="fb_published")
    _write_image(tmp_path, row)
    db = _FakeDerivativesDb(row, write_ok=False)
    _install(worker, monkeypatch, db)
    _live_ig_post(worker, monkeypatch, unlink_error=OSError("path is a directory"))
    published = _recorded(monkeypatch, worker)

    assert worker._do_one(_DID, "ig", dry_run=False) == "wedged"
    assert "release_publish_claim" not in db.names
    assert published[0]["permalink"] == "https://instagram.com/p/1"


# ─────────────────────────────────────────────────────────────────────────────
# CLI


def test_main_exit_codes_follow_the_outcome(worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = iter(["published", "skipped", "claim_lost", "dry_run", "error", "wedged"])
    monkeypatch.setattr(worker, "_do_one", lambda *_a, **_k: next(outcomes))
    argv = ["--derivative-id", _DID, "--platform", "fb"]

    assert [worker.main(argv) for _ in range(6)] == [0, 0, 0, 0, 1, 1]


def test_main_passes_the_dry_run_flag_through(worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        worker,
        "_do_one",
        lambda did, platform, *, dry_run: (
            seen.update(id=did, platform=platform, dry_run=dry_run) or "dry_run"
        ),
    )

    assert worker.main(["--derivative-id", _DID, "--platform", "ig", "--dry-run"]) == 0
    assert seen == {"id": _DID, "platform": "ig", "dry_run": True}
