"""Guard: no credential is ever passed as a URL query parameter.

httpx logs the full request URL at INFO level, so a key in the query string
lands in application logs, in CI output, and in whatever a user pastes into
a bug report. This repo has shipped that bug twice -- once in
`lib/gemini_client.py` and once across ten `recipe-publisher` modules --
so it gets a test rather than a code-review convention.

The check parses each file's AST and inspects the `params=` argument of
outgoing HTTP calls. It deliberately does NOT grep source text: an earlier
text-based version of this guard matched the word `?key=` inside its own
explanatory docstring and failed on a clean tree.
"""
# ruff: noqa: S101

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]

# Names that must never appear as a query-parameter key. These are the
# LLM-provider style keys; every one of them is fixed as of this commit, so
# the count is enforced at zero.
API_KEY_PARAMS = frozenset({"key", "api_key", "apikey", "secret"})

# Meta's Graph API is conventionally called with `access_token` in the query
# string, and every such call in this repo predates this guard. They leak the
# same way and should move to an Authorization header, but that is a change
# to the live publishing path and belongs in its own PR (tracked in ROADMAP).
# Pinned by FILE rather than line so refactors inside these modules do not
# churn the test, while a NEW module doing it still fails.
TOKEN_PARAMS = frozenset({"access_token", "token"})
KNOWN_TOKEN_FILES = frozenset(
    {
        "lib/keyword_research.py",
        "lib/oauth/facebook.py",
        "recipe-publisher/publishers/facebook.py",
        "recipe-publisher/publishers/instagram.py",
        "recipe-publisher/workers/worker_fb_post.py",
        "recipe-publisher/workers/worker_post_stories.py",
    }
)

CREDENTIAL_KEYS = API_KEY_PARAMS | TOKEN_PARAMS

# Directories with no first-party source in them.
SKIP_PARTS = frozenset(
    {".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)


def _source_files() -> list[Path]:
    return [p for p in APP_ROOT.rglob("*.py") if not SKIP_PARTS.intersection(p.parts)]


def _credential_params(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, key) for every call passing a credential via `params=`."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "params" or not isinstance(kw.value, ast.Dict):
                continue
            for k in kw.value.keys:
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and k.value.lower() in CREDENTIAL_KEYS
                ):
                    found.append((node.lineno, k.value))
    return found


def _offenders(keys: frozenset[str]) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # not first-party parseable source; nothing to assert
        rel = path.relative_to(APP_ROOT).as_posix()
        for lineno, key in _credential_params(tree):
            if key.lower() in keys:
                out.append((rel, lineno, key))
    return out


def test_no_api_key_is_sent_as_a_query_parameter() -> None:
    """Zero tolerance: every LLM-provider key travels in a header."""
    offenders = _offenders(API_KEY_PARAMS)
    assert not offenders, (
        "API keys must travel in a header, never a URL query string "
        "(httpx logs URLs at INFO). Offending call sites:\n  "
        + "\n  ".join(f"{f}:{n} params={{'{k}': ...}}" for f, n, k in sorted(offenders))
    )


def test_access_token_query_params_do_not_spread_to_new_modules() -> None:
    """The Meta Graph calls are grandfathered, but the set must not grow."""
    seen = {f for f, _, _ in _offenders(TOKEN_PARAMS)}
    new = seen - KNOWN_TOKEN_FILES
    assert not new, (
        "New modules are passing a token in the query string. Use an "
        "Authorization header instead:\n  " + "\n  ".join(sorted(new))
    )


def test_guard_detects_a_planted_violation() -> None:
    """The guard must actually fail on the pattern it exists to catch."""
    planted = ast.parse('httpx.post(url, params={"key": secret}, json=body)')
    assert _credential_params(planted) == [(1, "key")]


def test_guard_ignores_benign_params() -> None:
    benign = ast.parse('httpx.get(url, params={"query": q, "per_page": 1})')
    assert _credential_params(benign) == []
