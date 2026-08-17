"""Tests for `lib.runtime.flow.run_flow` — the shared cron-flow runner.

This logic previously lived in each script's ``if __name__ == "__main__":``
block, where it could not be imported and therefore was never tested. Every
case below describes a decision the 21 entrypoints used to answer
inconsistently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.runtime.flow import run_flow, session_file_check
from lib.runtime.singleton import LockAcquisitionError


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture worker_runs writes and neutralise the real lock."""
    calls: dict[str, Any] = {"started": [], "completed": []}
    from lib.runtime import flow

    monkeypatch.setattr(
        flow, "record_start", lambda d, label, brand: calls["started"].append((label, brand))
    )
    monkeypatch.setattr(
        flow,
        "record_complete",
        lambda d, label, brand, status, msg="": calls["completed"].append((label, brand, status)),
    )

    class _Lock:
        def __init__(self, name: str) -> None:
            calls["lock"] = name

        def __enter__(self) -> _Lock:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(flow, "SingletonLock", _Lock)
    return calls


@pytest.fixture(autouse=True)
def _brand_env(brand_context: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_flow` resolves the brand itself; the fixture points it at tmp_path."""
    return None


# ------------------------------------------------------------ health check


def test_health_check_short_circuits_before_lock_and_records(
    recorder: dict[str, Any],
) -> None:
    """Probing a flow must never look like a run.

    Six flows used to write a run row before checking the flag, so a health
    probe left `worker_runs` claiming the flow had started.
    """
    code = run_flow(
        "probe-flow",
        lambda: pytest.fail("main must not run"),
        health_check=lambda: 0,
        argv=["prog", "--health-check"],
    )
    assert code == 0
    assert recorder["started"] == []
    assert recorder["completed"] == []
    assert "lock" not in recorder


def test_health_check_exit_code_propagates(recorder: dict[str, Any]) -> None:
    code = run_flow(
        "probe-flow",
        lambda: pytest.fail("main must not run"),
        health_check=lambda: 1,
        argv=["prog", "--health-check"],
    )
    assert code == 1


def test_health_check_flag_without_a_probe_reports_healthy(recorder: dict[str, Any]) -> None:
    code = run_flow("probe-flow", lambda: 0, argv=["prog", "--health-check"])
    assert code == 0


# ------------------------------------------------------------ success path


def test_success_records_start_then_complete(recorder: dict[str, Any]) -> None:
    code = run_flow("fb-engager", lambda: None, argv=["prog"])
    assert code == 0
    assert recorder["started"] == [("dogfood-fb-engager", recorder["started"][0][1])]
    assert [c[2] for c in recorder["completed"]] == ["success"]
    assert recorder["lock"] == "fb-engager"


def test_main_return_code_propagates(recorder: dict[str, Any]) -> None:
    assert run_flow("fb-engager", lambda: 3, argv=["prog"]) == 3
    assert [c[2] for c in recorder["completed"]] == ["success"]


def test_a_domain_object_return_is_success_not_exit_1(recorder: dict[str, Any]) -> None:
    """The bug this pins: `fb-engager`/`ig-engager` returned a `ScanReport`.

    `return code or 0` passed the truthy report through; `SystemExit(report)`
    treats a non-int as an error MESSAGE and exits 1. Every successful run of
    both daily engagement flows was recorded as a failure, with the report
    itself as the error text -- while `run_flow` had already written
    `worker_runs` as "success". The exit code must be an int, always.
    """

    class _ScanReport:
        def __bool__(self) -> bool:  # truthy, like a real report
            return True

    code = run_flow("fb-engager", lambda: _ScanReport(), argv=["prog"])  # type: ignore[arg-type,return-value]
    assert code == 0
    assert isinstance(code, int)
    assert recorder["completed"][-1][-1] == "success"


@pytest.mark.parametrize("returned", [True, False])
def test_bool_return_is_success_not_an_exit_code(returned: bool, recorder: dict[str, Any]) -> None:
    """`bool` is an `int` subclass, so `True` would otherwise exit 1.

    A flow returning True means "it worked" -- the opposite of what exit 1
    says. Neither bool is a status, so both mean success.
    """
    code = run_flow("fb-engager", lambda: returned, argv=["prog"])  # type: ignore[arg-type,return-value]
    assert code == 0


def test_zero_and_nonzero_int_returns_are_still_honoured(recorder: dict[str, Any]) -> None:
    """The hardening must not swallow a real exit code."""
    assert run_flow("fb-engager", lambda: 0, argv=["prog"]) == 0
    assert run_flow("fb-engager", lambda: 2, argv=["prog"]) == 2


def test_worker_label_override_is_honoured(recorder: dict[str, Any]) -> None:
    run_flow("fb-engager", lambda: None, argv=["prog"], worker_label="legacy-label")
    assert recorder["started"][0][0] == "legacy-label"


# ------------------------------------------------------------ failure path


def test_exception_is_recorded_then_reraised(recorder: dict[str, Any]) -> None:
    """The traceback must still reach the cron log; the row must say error."""

    def _boom() -> int:
        raise RuntimeError("scan blew up")

    with pytest.raises(RuntimeError, match="scan blew up"):
        run_flow("fb-engager", _boom, argv=["prog"])

    assert [c[2] for c in recorder["completed"]] == ["error"]


# ------------------------------------------------------------ held lock


def test_held_lock_is_success_and_writes_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """An overlapping cron tick is normal, not a failure.

    Some flows exited non-zero here, turning healthy pacing into a cron alert.
    `record_start` sits inside the lock, so a tick that never acquired it must
    leave the previous run's row untouched.
    """
    from lib.runtime import flow

    calls: list[str] = []
    monkeypatch.setattr(flow, "record_start", lambda *a: calls.append("start"))
    monkeypatch.setattr(flow, "record_complete", lambda *a, **k: calls.append("complete"))

    class _Held:
        def __init__(self, name: str) -> None:
            pass

        def __enter__(self) -> None:
            raise LockAcquisitionError("held", context={"name": "x"})

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(flow, "SingletonLock", _Held)

    assert run_flow("fb-engager", lambda: pytest.fail("must not run"), argv=["prog"]) == 0
    assert calls == []


# ------------------------------------------------------- session_file_check


def test_session_file_check_accepts_a_populated_file(tmp_path: Path) -> None:
    f = tmp_path / "session.json"
    f.write_text('{"cookies": []}')
    assert session_file_check(f, "FB") == 0


def test_session_file_check_rejects_missing_file(tmp_path: Path) -> None:
    assert session_file_check(tmp_path / "nope.json", "IG") == 1


def test_session_file_check_rejects_empty_json(tmp_path: Path) -> None:
    """A torn-down browser context can leave `{}` behind; that is not a session."""
    f = tmp_path / "session.json"
    f.write_text("{}")
    assert session_file_check(f, "IG") == 1


# ── every flow's `main` matches run_flow's contract ─────────────────────────
#
# mypy catches this ("Argument 2 to run_flow has incompatible type ... ->
# ScanReport | None; expected Callable[[], int | None]") -- but it never ran:
# scripts/fb_engager.py and scripts/ig_engager.py are in CI's lint and format
# lists and NOT its mypy list, because each carries 9 unrelated pre-existing
# errors. The type system already knew; the gate didn't. This test is the
# cheap stand-in that runs everywhere until those files are mypy-clean.

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
_VALID_MAIN_RETURNS = {"None", "int", "int | None", "Optional[int]"}


def _run_flow_main_annotations() -> list[tuple[str, str, str]]:
    """(file, main-function name, its return annotation) for each run_flow call."""
    import ast

    found: list[tuple[str, str, str]] = []
    for path in sorted(_SCRIPTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "run_flow"):
                continue
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Name):
                continue  # a lambda -- nothing to resolve statically
            fn = funcs.get(node.args[1].id)
            if fn is None:
                continue
            ann = ast.unparse(fn.returns) if fn.returns else "<unannotated>"
            found.append((path.name, fn.name, ann))
    return found


def test_every_flow_main_returns_an_exit_code_or_none() -> None:
    """A `main` returning a domain object silently becomes exit 1."""
    annotations = _run_flow_main_annotations()
    assert annotations, "expected to find run_flow call sites to check"
    bad = [(f, n, a) for f, n, a in annotations if a not in _VALID_MAIN_RETURNS]
    assert not bad, (
        "these run_flow mains do not return an exit code, so SystemExit will "
        f"treat the value as an error message and exit 1: {bad}"
    )
