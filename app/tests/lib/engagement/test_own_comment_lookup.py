"""Unit tests for the own-comment read primitive (Wave 0).

`lib.engagement.adapters.own_comment` is the only honest answer to "did our
comment land?", so its failure modes are the whole point: a lookup that cannot
see the page must say `inconclusive`, never `absent`. Reporting `absent` there
would release the claim and let the engager comment a second time — the
duplicate-comment bug this slice exists to fix.

Playwright never starts here. `_FakePage` implements the four members the
lookup touches (`goto` / `url` / `content` / `evaluate`), and the JS payloads
are compared by identity to assert the expand-then-find order.
"""

from __future__ import annotations

# ruff: noqa: S101
#   S101 — pytest tests use `assert` by design (project convention).
import pytest

from lib.engagement.adapters.facebook_own_comment_dom import (
    FIND_OWN_COMMENT_FB_JS,
    LOAD_ALL_FB_COMMENTS_JS,
)
from lib.engagement.adapters.instagram_own_comment_dom import (
    FIND_OWN_COMMENT_IG_JS,
    LOAD_ALL_IG_COMMENTS_JS,
)
from lib.engagement.adapters.own_comment import (
    MATCH_PREFIX_CHARS,
    find_own_comment_fb,
    find_own_comment_ig,
    normalize_for_match,
)

_IG_URL = "https://www.instagram.com/p/abc123/"
_FB_URL = "https://www.facebook.com/groups/1/posts/2/"
_TEXT = "We tried this with Nalla on a 3-mile trail run and her paws held up fine."


class _FakeNavTimeoutError(Exception):
    """Stands in for playwright's TimeoutError — only its class name matters."""


class _FakePage:
    """Minimal Playwright `Page` stand-in for the four members `_lookup` uses."""

    def __init__(
        self,
        *,
        count: int = 0,
        landing_url: str | None = None,
        body: str = "<html><body>a post</body></html>",
        goto_error: Exception | None = None,
        evaluate_error: Exception | None = None,
        find_result: object | None = None,
    ) -> None:
        self.url = "about:blank"
        self._count = count
        self._landing_url = landing_url
        self._body = body
        self._goto_error = goto_error
        self._evaluate_error = evaluate_error
        self._find_result = find_result
        self.scripts: list[str] = []
        self.goto_calls: list[str] = []

    def goto(self, url: str, **_kwargs: object) -> None:
        if self._goto_error is not None:
            raise self._goto_error
        self.goto_calls.append(url)
        self.url = self._landing_url or url

    def content(self) -> str:
        return self._body

    def evaluate(self, script: str, arg: object = None) -> object:
        if self._evaluate_error is not None:
            raise self._evaluate_error
        self.scripts.append(script)
        if script in (FIND_OWN_COMMENT_IG_JS, FIND_OWN_COMMENT_FB_JS):
            if self._find_result is not None:
                return self._find_result
            return {"count": self._count}
        return self._count


def test_ig_reports_found_when_the_dom_matches_our_text() -> None:
    page = _FakePage(count=1)

    lookup = find_own_comment_ig(page, _IG_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.present is True
    assert lookup.conclusive is True
    assert lookup.count == 1
    assert lookup.reason == "ok"
    assert page.goto_calls == [_IG_URL]
    # Expand every comment BEFORE counting, or a lazily-rendered comment of
    # ours reads as absent.
    assert page.scripts == [LOAD_ALL_IG_COMMENTS_JS, FIND_OWN_COMMENT_IG_JS]


def test_ig_reports_every_copy_when_the_post_already_has_duplicates() -> None:
    page = _FakePage(count=2)

    lookup = find_own_comment_ig(page, _IG_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.present is True
    assert lookup.count == 2


def test_fb_reports_absent_when_the_count_is_zero() -> None:
    page = _FakePage(count=0)

    lookup = find_own_comment_fb(page, _FB_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.conclusive is True  # we DID read the comments
    assert lookup.present is False
    assert lookup.count == 0
    assert lookup.reason == "ok"
    assert page.scripts == [LOAD_ALL_FB_COMMENTS_JS, FIND_OWN_COMMENT_FB_JS]


@pytest.mark.parametrize(
    "lookup_fn,url",
    [(find_own_comment_ig, _IG_URL), (find_own_comment_fb, _FB_URL)],
)
def test_navigation_failure_is_inconclusive_not_absent(lookup_fn: object, url: str) -> None:
    """The test that matters most.

    A nav timeout means we never saw the page. If that came back conclusive-
    and-absent, the caller would release its claim and comment a second time
    on a post it may have already commented on — exactly the bug.
    """
    page = _FakePage(goto_error=_FakeNavTimeoutError("Timeout 30000ms exceeded"))

    lookup = lookup_fn(page, url, _TEXT)  # type: ignore[operator]

    assert lookup.conclusive is False
    assert lookup.present is False
    assert lookup.reason == "inconclusive:nav:_FakeNavTimeoutError"
    # count is 0, but it means "unknown" — conclusive is the field that decides.
    assert lookup.count == 0
    assert page.scripts == []  # never got as far as reading the DOM


def test_login_wall_is_inconclusive() -> None:
    page = _FakePage(count=0, landing_url="https://www.instagram.com/accounts/login/")

    lookup = find_own_comment_ig(page, _IG_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.conclusive is False
    assert lookup.reason == "inconclusive:login_wall"
    assert page.scripts == []


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>Sorry, this page isn't available.</body></html>",
        "<html><body>Sorry, this page isn’t available.</body></html>",
        "<html><body>This content isn't available right now</body></html>",
    ],
)
def test_post_unavailable_is_inconclusive(body: str) -> None:
    """A deleted post tells us nothing about whether we commented on it."""
    page = _FakePage(count=0, body=body)

    lookup = find_own_comment_fb(page, _FB_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.conclusive is False
    assert lookup.reason == "inconclusive:post_unavailable"
    assert page.scripts == []


def test_a_broken_dom_evaluate_is_inconclusive() -> None:
    page = _FakePage(evaluate_error=RuntimeError("Execution context destroyed"))

    lookup = find_own_comment_ig(page, _IG_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.conclusive is False
    assert lookup.reason == "inconclusive:evaluate:RuntimeError"


def test_an_unreadable_finder_payload_is_inconclusive() -> None:
    """A finder that returns junk is a failed read, not an empty comment list."""
    page = _FakePage(find_result="not-a-dict")

    lookup = find_own_comment_ig(page, _IG_URL, _TEXT)  # type: ignore[arg-type]

    assert lookup.conclusive is False
    assert lookup.reason == "inconclusive:unreadable_result"


def test_matcher_ignores_whitespace_and_matches_an_80_char_prefix() -> None:
    messy = "  We   tried\n\tthis with Nalla\r\non a trail run.  "

    assert normalize_for_match(messy) == "we tried this with nalla on a trail run."
    # Case-insensitive: the same sentence shouted is the same comment.
    assert normalize_for_match(messy.upper()) == normalize_for_match(messy)

    long_text = "x" * 500
    assert len(normalize_for_match(long_text)) == MATCH_PREFIX_CHARS == 80

    # Only the prefix is load-bearing — a platform-truncated or edited tail
    # must still match, because a missed match is the expensive direction.
    head = (
        "Nalla ate this exact meal every day for a week and here is what "
        "actually changed for her coat"
    )
    assert len(head) > MATCH_PREFIX_CHARS
    assert normalize_for_match(head + " ...plus a tail") == normalize_for_match(head)


def test_empty_text_is_inconclusive_not_absent() -> None:
    """An empty draft can't be searched for; refusing to answer is the safe read."""
    page = _FakePage(count=0)

    lookup = find_own_comment_ig(page, _IG_URL, "   \n  ")  # type: ignore[arg-type]

    assert lookup.conclusive is False
    assert lookup.reason == "inconclusive:empty_text"
    assert page.goto_calls == []
