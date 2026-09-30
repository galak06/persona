"""Characterization of the release sweep -- the LIVE publishing path.

Written BEFORE `_publish_one`/`_release_due` moved out of
`scripts/crewai_social_posts_pipeline.py` and run green against the unmoved
code first, so it pins today's behaviour rather than tomorrow's intention; the
derivative-track cases were added only once the regular track was locked down.
The sweep is what actually posts to Facebook and Instagram; a silent drift in
the argv, the working directory, the injected `BRAND_DIR` or the FB-before-IG
ordering does not fail loudly -- it publishes nothing, or publishes twice.

Everything real is faked: `subprocess.run` never launches a worker, and the
due-row readers never touch Postgres. The seam is deliberately narrow -- the
sweep is asserted end to end through `release_due`, exactly as the cron flow
reaches it, so the test survived the move unchanged.
"""
# ruff: noqa: S101  (pytest tests use `assert` by design)

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lib import derivatives_db, social_post_db
from lib.social_release import DERIVATIVE_WORKER, REGULAR_WORKER, release_due

_APP_ROOT = Path(__file__).resolve().parent.parent
_BRAND_DIR = Path("/tmp/brand-under-test")  # noqa: S108 - never written to


class _Call:
    """One recorded `subprocess.run` invocation."""

    def __init__(self, argv: list[str], kwargs: dict[str, Any]) -> None:
        self.argv = argv
        self.kwargs = kwargs

    @property
    def module(self) -> str:
        return self.argv[self.argv.index("-m") + 1]

    @property
    def platform(self) -> str:
        return self.argv[self.argv.index("--platform") + 1]

    @property
    def track(self) -> str:
        """`"fb-regular"`-style label, for readable ordering assertions."""
        kind = "derivative" if self.module == DERIVATIVE_WORKER else "regular"
        return f"{self.platform}-{kind}"


class _Result:
    def __init__(self, *, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class _FakeRun:
    """Records every call; `failures` maps a row id to the outcome to fake."""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.calls: list[_Call] = []
        self.failures = failures or {}

    def __call__(self, argv: list[str], **kwargs: Any) -> _Result:
        self.calls.append(_Call(argv, kwargs))
        row_id = argv[argv.index("-m") + 3]  # the value after the id flag
        mode = self.failures.get(row_id, "ok")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=600)
        if mode == "nonzero":
            return _Result(returncode=1, stderr="boom")
        return _Result(returncode=0, stderr="")


def _rows(ids: Sequence[str]) -> list[dict[str, Any]]:
    return [{"id": rid} for rid in ids]


@pytest.fixture
def due(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fake the due-row readers of BOTH tables; default: nothing is due.

    `derivatives_db` is patched on the package, which is the only reason the
    sweep's call-time attribute lookup matters.
    """

    def _set(
        *,
        fb: Sequence[str] = (),
        ig: Sequence[str] = (),
        derivative_fb: Sequence[str] = (),
        derivative_ig: Sequence[str] = (),
    ) -> None:
        pairs = (
            (social_post_db, "list_due_for_fb", fb),
            (social_post_db, "list_due_for_ig", ig),
            (derivatives_db, "list_due_for_fb", derivative_fb),
            (derivatives_db, "list_due_for_ig", derivative_ig),
        )
        for module, name, ids in pairs:
            rows = _rows(ids)
            monkeypatch.setattr(module, name, lambda _r=rows, **_kw: _r, raising=True)

    _set()
    return _set


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(failures: dict[str, str] | None = None) -> _FakeRun:
        fake = _FakeRun(failures)
        monkeypatch.setattr(subprocess, "run", fake, raising=True)
        return fake

    return _install


# ── the regular track: exactly what shipped before the move ──────────────


def test_regular_fb_half_invokes_the_worker_module_verbatim(due: Any, run: Any) -> None:
    """The argv is the contract with `worker_wp_ideas_social_post`; it is a
    `-m` module run, not a file path, because the worker imports `publishers.*`
    relative to `recipe-publisher/`."""
    due(fb=["idea-1"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert len(fake.calls) == 1
    assert fake.calls[0].argv == [
        sys.executable,
        "-m",
        "workers.worker_wp_ideas_social_post",
        "--idea-id",
        "idea-1",
        "--platform",
        "fb",
    ]
    assert REGULAR_WORKER == "workers.worker_wp_ideas_social_post"


def test_worker_runs_from_recipe_publisher_with_brand_dir_injected(due: Any, run: Any) -> None:
    """`cwd` is what makes `workers.*` and `publishers.*` importable at all,
    and `BRAND_DIR` is how the subprocess learns which brand it is publishing
    for -- the parent's own environment is otherwise passed through whole."""
    due(fb=["idea-1"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    kwargs = fake.calls[0].kwargs
    assert kwargs["cwd"] == _APP_ROOT / "recipe-publisher"
    assert kwargs["env"]["BRAND_DIR"] == str(_BRAND_DIR)
    assert kwargs["env"]["PATH"] == os.environ["PATH"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 600


def test_ig_half_passes_the_ig_platform(due: Any, run: Any) -> None:
    due(ig=["idea-9"])
    fake = run()

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert fake.calls[0].argv[-2:] == ["--platform", "ig"]
    assert outcomes == {"ig_published": 1}


def test_every_fb_half_runs_before_any_ig_half(due: Any, run: Any) -> None:
    """A row that publishes to FB in this pass arms its IG due-time as a side
    effect, always in the future, so it cannot also fire in the same pass.
    Interleaving the two passes would reopen that door."""
    due(fb=["a", "b"], ig=["c", "d"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert [c.platform for c in fake.calls] == ["fb", "fb", "ig", "ig"]


def test_outcome_keys_are_fb_published_and_ig_published(due: Any, run: Any) -> None:
    due(fb=["a", "b"], ig=["c"])
    run()

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {"fb_published": 2, "ig_published": 1}


def test_a_nonzero_exit_is_an_error_and_the_sweep_carries_on(due: Any, run: Any) -> None:
    """One wedged row must not strand every later row past its slot."""
    due(fb=["bad", "good"], ig=["later"])
    fake = run({"bad": "nonzero"})

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {"error": 1, "fb_published": 1, "ig_published": 1}
    assert len(fake.calls) == 3


def test_a_timeout_is_an_error_and_the_sweep_carries_on(due: Any, run: Any) -> None:
    """`TimeoutExpired` escapes `subprocess.run` rather than returning a
    result, so it needs its own guard -- and it is a failure, not a crash."""
    due(fb=["slow", "good"])
    fake = run({"slow": "timeout"})

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {"error": 1, "fb_published": 1}
    assert len(fake.calls) == 2


def test_nothing_due_publishes_nothing(due: Any, run: Any) -> None:
    fake = run()

    assert release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR) == {}
    assert fake.calls == []


# ── the derivative track: a second table, a second worker ────────────────


def test_derivative_rows_go_to_their_own_worker_and_id_flag(due: Any, run: Any) -> None:
    """A spotlight is a `content_derivatives` row, not a `content_ideas` one --
    `--idea-id` would silently address a different table's primary key."""
    due(derivative_fb=["deriv-1"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert fake.calls[0].argv == [
        sys.executable,
        "-m",
        "workers.worker_social_derivative",
        "--derivative-id",
        "deriv-1",
        "--platform",
        "fb",
    ]
    assert DERIVATIVE_WORKER == "workers.worker_social_derivative"


def test_derivative_halves_share_the_regular_cwd_env_and_timeout(due: Any, run: Any) -> None:
    due(derivative_ig=["deriv-2"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    kwargs = fake.calls[0].kwargs
    assert kwargs["cwd"] == _APP_ROOT / "recipe-publisher"
    assert kwargs["env"]["BRAND_DIR"] == str(_BRAND_DIR)
    assert kwargs["timeout"] == 600
    assert fake.calls[0].argv[-2:] == ["--platform", "ig"]


def test_the_four_passes_run_fb_first_then_ig_never_interleaved(due: Any, run: Any) -> None:
    """Both tables publish FB before IG for the same reason, so the sweep
    orders by PLATFORM first and table second: FB regular, FB derivative, IG
    regular, IG derivative."""
    due(fb=["a"], ig=["b"], derivative_fb=["c"], derivative_ig=["d"])
    fake = run()

    release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert [c.track for c in fake.calls] == [
        "fb-regular",
        "fb-derivative",
        "ig-regular",
        "ig-derivative",
    ]


def test_each_track_gets_its_own_success_key(due: Any, run: Any) -> None:
    """Separate success keys so the operator can see which track published;
    they all read as success to the caller's `_exit_code`."""
    due(fb=["a"], ig=["b"], derivative_fb=["c"], derivative_ig=["d"])
    run()

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {
        "fb_published": 1,
        "ig_published": 1,
        "derivative_fb_published": 1,
        "derivative_ig_published": 1,
    }


def test_a_failing_derivative_does_not_stop_the_regular_track(due: Any, run: Any) -> None:
    due(fb=["idea-ok"], ig=["idea-ok-2"], derivative_fb=["deriv-bad"])
    fake = run({"deriv-bad": "nonzero"})

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {"fb_published": 1, "ig_published": 1, "error": 1}
    assert [c.track for c in fake.calls] == ["fb-regular", "fb-derivative", "ig-regular"]


def test_a_failing_regular_post_does_not_stop_the_derivative_track(due: Any, run: Any) -> None:
    due(fb=["idea-bad"], derivative_fb=["deriv-ok"], derivative_ig=["deriv-ok-2"])
    fake = run({"idea-bad": "timeout"})

    outcomes = release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR)

    assert outcomes == {
        "derivative_fb_published": 1,
        "derivative_ig_published": 1,
        "error": 1,
    }
    assert len(fake.calls) == 3


def test_failures_from_both_tracks_share_one_error_key(due: Any, run: Any) -> None:
    """One shared key on purpose: `_exit_code` counts every non-`error` key as
    a success, so a per-track error key would make a run of pure failures look
    like a successful run."""
    due(fb=["idea-bad"], derivative_fb=["deriv-bad"])
    run({"idea-bad": "nonzero", "deriv-bad": "nonzero"})

    assert release_due(brand_id="dogfoodandfun", brand_dir=_BRAND_DIR) == {"error": 2}
