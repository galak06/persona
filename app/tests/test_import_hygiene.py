"""Guard against the dual-module-identity footgun coming back.

Until 2026-08-15 `pyproject.toml` set `pythonpath = ["lib"]` and ~40 scripts
added `app/lib` to `sys.path` themselves. Both `import rate_limiter` and
`import lib.rate_limiter` then worked, and Python built **two distinct module
objects from one source file**. Monkeypatching one silently missed what the
code under test actually read, which is why the engager tests needed a
bespoke "patch the bare-name modules" helper.

There is one import root now: `app`, everything under the `lib.` namespace.
These tests fail loudly if either half of the footgun returns -- a bare import
of a `lib` module, or the `pythonpath` setting that makes one resolve.

See `docs/adr/0006-single-import-root.md`.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SCANNED = ("lib", "scripts", "api", "tools", "tests")

# `lib/io/` shadows the stdlib `io`; every real use of it is already `lib.io.*`,
# so a bare `import io` is stdlib and must not be flagged.
STDLIB_SHADOWS = {"io"}


def _lib_top_level_names() -> set[str]:
    """Every name importable bare if `app/lib` were on sys.path."""
    lib = APP / "lib"
    names = {p.stem for p in lib.glob("*.py") if p.stem != "__init__"}
    names |= {d.name for d in lib.iterdir() if d.is_dir() and (d / "__init__.py").exists()}
    return names - STDLIB_SHADOWS


def _python_files() -> list[Path]:
    out: list[Path] = []
    for d in SCANNED:
        for py in (APP / d).rglob("*.py"):
            if "__pycache__" in str(py) or ".venv" in str(py):
                continue
            out.append(py)
    return sorted(out)


def _bare_lib_imports(path: Path, names: set[str]) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a syntax error is another test's problem
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in names:
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in names:
                    found.append((node.lineno, f"import {alias.name}"))
    return found


def test_no_bare_lib_imports() -> None:
    """Every `lib` module must be imported through the `lib.` namespace."""
    names = _lib_top_level_names()
    offenders: list[str] = []
    for py in _python_files():
        for lineno, stmt in _bare_lib_imports(py, names):
            offenders.append(f"{py.relative_to(APP)}:{lineno}: {stmt}")
    assert not offenders, (
        "bare imports of lib modules found -- these create a second module object "
        "for the same file (see docs/adr/0006-single-import-root.md):\n  "
        + "\n  ".join(offenders)
    )


def test_pytest_does_not_put_lib_on_the_path() -> None:
    """`pythonpath` must not re-add `lib`, which is what made bare imports resolve."""
    cfg = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))
    pythonpath = cfg["tool"]["pytest"]["ini_options"].get("pythonpath", [])
    assert "lib" not in pythonpath, (
        "pyproject.toml re-added 'lib' to pytest's pythonpath; that reintroduces "
        "the dual-module-identity footgun (ADR 0006)."
    )


def test_no_source_file_adds_lib_to_sys_path() -> None:
    """No module may put `app/lib` on sys.path at runtime either.

    Checks the *value* being inserted, not just a `"lib"` literal: the last
    offender found (`tests/test_commenter.py`) built the path into a `_LIB`
    variable first, which a literal-only scan walks straight past.
    """
    offenders: list[str] = []
    here = Path(__file__).resolve()
    for py in _python_files():
        if py.resolve() == here:  # this file names the pattern it looks for
            continue
        text = py.read_text(encoding="utf-8")
        # names bound to a path ending in /lib -- e.g. `_LIB = ROOT / "lib"`
        lib_vars = set(re.findall(r'^\s*(\w+)\s*=\s*[^\n#]*/\s*"lib"', text, re.M))
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if "sys.path" not in stripped or stripped.startswith("#"):
                continue
            if '"lib"' in stripped or any(v in stripped for v in lib_vars):
                offenders.append(f"{py.relative_to(APP)}:{i}: {stripped}")
    assert not offenders, (
        "these lines put app/lib on sys.path, which makes bare imports resolve "
        "again (ADR 0006):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", ["rate_limiter", "deduplication", "notifier", "config"])
def test_bare_name_is_not_importable(name: str) -> None:
    """The bare names must simply not resolve any more."""
    with pytest.raises(ModuleNotFoundError):
        __import__(name)
