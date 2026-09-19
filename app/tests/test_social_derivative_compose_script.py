"""The compose script's safety property and its environment pre-flight.

`scripts/social_derivative_compose.py` is the file the API dispatches by name,
with arguments the API chooses. The guarantee that matters is therefore
structural rather than behavioural: nothing that posts to a platform is
reachable from it, so no argument shape and no row state can make a spotlight
publish itself. That is asserted over the parsed imports of the script AND of
every module in `lib/crew/spotlight/`, because an import two hops away is just
as reachable as one in the script itself.

The pre-flight half is here for a subtler reason. A missing env var arrives
AFTER the API has already minted the row as `'composing'` and the review page
has already started polling it, so a script that merely exits leaves the
operator watching a spinner for the twenty minutes the stale sweep takes to
call it a timeout -- and then tells them the wrong thing.
"""
# ruff: noqa: S101

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts import social_derivative_compose as script

from lib.crew.spotlight.result import SpotlightResult

_APP_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _APP_ROOT / "scripts" / "social_derivative_compose.py"
_PACKAGE = _APP_ROOT / "lib" / "crew" / "spotlight"

#: Publishing lives in `recipe-publisher/publishers/*`, is driven by
#: `workers/worker_*`, and is swept by `lib/social_release.py`. One substring
#: each, matched against the dotted module name rather than the file text --
#: the modules' own docstrings say all three words out loud.
_FORBIDDEN = ("publish", "worker", "release")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _spotlight_modules() -> list[Path]:
    return sorted(p for p in _PACKAGE.glob("*.py"))


def test_the_script_and_the_package_import_nothing_that_can_publish() -> None:
    """THE SAFETY PROPERTY, over the whole compose package rather than one file.

    Not an import-lightness claim: `post_products` reaches the crewai selector
    stack through `lib.crew.products`, and `compose` reaches the writer crew
    and the image model on purpose. What may not appear is a path to a
    platform.
    """
    for path in [_SCRIPT, *_spotlight_modules()]:
        offenders = {
            name for name in _imported_modules(path) if any(word in name for word in _FORBIDDEN)
        }
        assert offenders == set(), f"{path.name} imports {sorted(offenders)}"


def test_the_package_reaches_the_derivatives_table_only_through_the_package() -> None:
    """`lib.derivatives_db.publish` holds `claim_publish`/`set_fb_result`. The
    compose path imports the PACKAGE, so those never come into scope -- which
    is also why the check above can treat 'publish' as a forbidden substring
    without tripping over the table this feature is built on."""
    for path in _spotlight_modules():
        names = _imported_modules(path)
        assert not any(name.startswith("lib.derivatives_db.") for name in names), path.name


def test_the_script_delegates_to_compose_and_owns_no_logic_of_its_own() -> None:
    """Everything a spotlight does is in the library, where it is tested. A
    second implementation behind a CLI flag is how the two drift apart."""
    assert "lib.crew.spotlight.compose" in _imported_modules(_SCRIPT)


# ── the environment pre-flight ──────────────────────────────────────────────

_REQUIRED = (
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "WP_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
    "AMAZON_ASSOCIATES_TAG",
)


@pytest.fixture()
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """The script with its world faked: no env files read, no composition run."""
    state: dict[str, Any] = {
        "result": SpotlightResult(True, "composed", image_path="x.jpg", source="gemini"),
        "failed": [],
        "calls": [],
    }

    monkeypatch.setattr(script, "load_brand_env_into_environ", lambda _dir: None)
    monkeypatch.setattr(script, "load_local_env", lambda: None)

    def _compose(derivative_id: str, **kwargs: Any) -> SpotlightResult:
        state["calls"].append({"derivative_id": derivative_id, **kwargs})
        result: SpotlightResult = state["result"]
        return result

    def _mark_failed(derivative_id: str, *, error: str) -> bool:
        state["failed"].append({"id": derivative_id, "error": error})
        return True

    monkeypatch.setattr(script, "compose_spotlight", _compose)
    monkeypatch.setattr(script.derivatives_db, "mark_failed", _mark_failed)
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    for name in _REQUIRED:
        monkeypatch.setenv(name, "set")
    monkeypatch.setattr(sys, "argv", ["prog", "--derivative-id", "deriv-1"])
    state["brand_dir"] = tmp_path
    return state


def test_a_composed_spotlight_exits_zero_with_a_machine_readable_summary(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The `summary:` line lands in `worker_runs.message` and from there in the
    operator's toast, so it carries the reason rather than a log level."""
    assert script.main() == 0

    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(line.removeprefix("summary: ")) == {
        "ok": True,
        "reason": "composed",
        "image_path": "x.jpg",
        "source": "gemini",
    }
    assert cli["calls"] == [
        {"derivative_id": "deriv-1", "brand_dir": cli["brand_dir"], "dry_run": False}
    ]


def test_a_failed_composition_exits_one(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The worker reads the exit code; a composition that failed must not be
    recorded as a successful run."""
    cli["result"] = SpotlightResult(False, "no_reference_photo")

    assert script.main() == 1
    assert "no_reference_photo" in capsys.readouterr().out


def test_dry_run_is_passed_through_rather_than_reimplemented(
    cli: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One dry-run implementation, in the library where it is tested."""
    monkeypatch.setattr(sys, "argv", ["prog", "--derivative-id", "deriv-1", "--dry-run"])

    assert script.main() == 0
    assert cli["calls"][0]["dry_run"] is True


@pytest.mark.parametrize("missing", _REQUIRED)
def test_a_missing_env_var_fails_the_row_instead_of_hanging_it(
    cli: dict[str, Any], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Including AMAZON_ASSOCIATES_TAG, which only this compose needs: a
    spotlight whose whole purpose is an affiliate link cannot run without one,
    and the row must say so rather than wait out the stale sweep."""
    monkeypatch.delenv(missing)

    assert script.main() == 1
    assert cli["failed"] == [{"id": "deriv-1", "error": "missing_env"}]
    assert cli["calls"] == []


def test_a_dry_run_with_a_broken_environment_marks_no_row(
    cli: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run changes no row on its way through, so it may not fail one on
    its way out -- the row is still composing for a real run to pick up."""
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr(sys, "argv", ["prog", "--derivative-id", "deriv-1", "--dry-run"])

    assert script.main() == 1
    assert cli["failed"] == []


def test_without_a_brand_dir_nothing_runs(
    cli: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BRAND_DIR decides which brand's library, catalog and pending directory
    a run touches. Guessing it would compose against the wrong brand."""
    monkeypatch.delenv("BRAND_DIR")

    assert script.main() == 1
    assert cli["calls"] == []
    assert cli["failed"] == []
