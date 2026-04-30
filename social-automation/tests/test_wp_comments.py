"""Tests for the WordPress comment moderation pipeline.

Covers:
  - wp_scan.is_obvious_spam heuristic (false positives are the real risk)
  - wp_scan.strip_html HTML → plain text
  - comment_poster.post_comment_wp approve + reply flow (httpx mocked)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import respx
from httpx import Response

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import wp_scan
from comment_poster import post_comment_wp


class TestIsObviousSpam:
    def test_genuine_dog_question_is_not_spam(self):
        body = (
            "Hi! Is this recipe safe for a 7-month old puppy? "
            "Mine is a beagle mix and I wasn't sure about the peanut butter amount."
        )
        assert wp_scan.is_obvious_spam(body) == (False, "")

    def test_three_links_flagged(self):
        body = (
            "Check out https://example.com and also http://foo.com "
            "and https://bar.net for more info."
        )
        is_spam, reason = wp_scan.is_obvious_spam(body)
        assert is_spam is True
        assert "links" in reason

    def test_two_links_not_flagged(self):
        # A real commenter linking to one or two sources shouldn't trip the
        # heuristic — we'd rather queue for Telegram than auto-trash.
        body = "Also see https://aspca.org and https://akc.org for more on this."
        assert wp_scan.is_obvious_spam(body) == (False, "")

    def test_spam_keyword_flagged(self):
        body = "Great post! Also check out my forex signals."
        is_spam, reason = wp_scan.is_obvious_spam(body)
        assert is_spam is True
        assert "forex" in reason

    def test_suspicious_tld_flagged(self):
        body = "Nice recipe, thanks!"
        is_spam, reason = wp_scan.is_obvious_spam(body, author_url="http://bigwin.win")
        assert is_spam is True
        assert ".win" in reason

    def test_normal_tld_not_flagged(self):
        body = "Nice recipe, thanks!"
        assert wp_scan.is_obvious_spam(body, author_url="https://mydogblog.com") == (False, "")


class TestIsSelfPingback:
    def test_pingback_from_own_domain_is_self(self):
        c = {
            "type": "pingback",
            "author_url": "https://dogfoodandfun.com/some-post/",
        }
        assert wp_scan.is_self_pingback(c, "https://dogfoodandfun.com") is True

    def test_pingback_from_external_domain_is_not_self(self):
        c = {
            "type": "pingback",
            "author_url": "https://other-site.com/my-review/",
        }
        assert wp_scan.is_self_pingback(c, "https://dogfoodandfun.com") is False

    def test_regular_comment_never_classified_as_self_pingback(self):
        # Even if a commenter's author_url is on our domain (e.g. a staff
        # member), a type=comment item must never be auto-trashed — only
        # pingbacks/trackbacks go down this path.
        c = {
            "type": "comment",
            "author_url": "https://dogfoodandfun.com",
        }
        assert wp_scan.is_self_pingback(c, "https://dogfoodandfun.com") is False

    def test_http_https_and_www_variants_all_match(self):
        for base, url in [
            ("https://dogfoodandfun.com", "http://dogfoodandfun.com/x"),
            ("https://dogfoodandfun.com", "https://www.dogfoodandfun.com/x"),
            ("http://dogfoodandfun.com", "https://dogfoodandfun.com/x"),
        ]:
            c = {"type": "pingback", "author_url": url}
            assert wp_scan.is_self_pingback(c, base) is True, f"{base} vs {url}"

    def test_trackback_from_own_domain_also_trashed(self):
        c = {
            "type": "trackback",
            "author_url": "https://dogfoodandfun.com/foo",
        }
        assert wp_scan.is_self_pingback(c, "https://dogfoodandfun.com") is True

    def test_missing_author_url_is_not_self(self):
        c = {"type": "pingback", "author_url": ""}
        assert wp_scan.is_self_pingback(c, "https://dogfoodandfun.com") is False


class TestStripHtml:
    def test_strips_paragraph_tags(self):
        assert wp_scan.strip_html("<p>hello world</p>") == "hello world"

    def test_collapses_whitespace(self):
        assert wp_scan.strip_html("<p>hello</p>\n\n<p>world</p>") == "hello world"

    def test_handles_empty_input(self):
        assert wp_scan.strip_html("") == ""
        assert wp_scan.strip_html(None) == ""  # type: ignore[arg-type]


@pytest.fixture
def wp_env(monkeypatch):
    monkeypatch.setenv("WP_URL", "https://example.test")
    monkeypatch.setenv("WP_USER", "claude_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "abcd efgh ijkl mnop")


class TestPostCommentWp:
    @respx.mock
    def test_happy_path_approves_then_replies(self, wp_env):
        approve_route = respx.post("https://example.test/wp-json/wp/v2/comments/42").mock(
            return_value=Response(200, json={"id": 42, "status": "approved"})
        )
        reply_route = respx.post("https://example.test/wp-json/wp/v2/comments").mock(
            return_value=Response(
                201,
                json={
                    "id": 77,
                    "link": "https://example.test/post/#comment-77",
                },
            )
        )

        ok, detail = post_comment_wp("42", 100, "Thanks for asking, Sam!")

        assert ok is True
        assert detail == "https://example.test/post/#comment-77"
        assert approve_route.called
        assert reply_route.called
        # Reply must carry the right post + parent linkage. httpx serializes
        # JSON without whitespace, so match the compact form.
        body = reply_route.calls.last.request.read().decode()
        assert '"post":100' in body
        assert '"parent":42' in body

    @respx.mock
    def test_approve_failure_short_circuits(self, wp_env):
        respx.post("https://example.test/wp-json/wp/v2/comments/42").mock(
            return_value=Response(403, text="forbidden")
        )
        reply_route = respx.post("https://example.test/wp-json/wp/v2/comments").mock(
            return_value=Response(201, json={"id": 77, "link": ""})
        )

        ok, detail = post_comment_wp("42", 100, "hi")

        assert ok is False
        assert "approve failed" in detail
        assert "403" in detail
        # Critical: reply must NOT be posted if the visitor comment wasn't approved.
        assert not reply_route.called

    @respx.mock
    def test_reply_failure_reported(self, wp_env):
        respx.post("https://example.test/wp-json/wp/v2/comments/42").mock(
            return_value=Response(200, json={"id": 42})
        )
        respx.post("https://example.test/wp-json/wp/v2/comments").mock(
            return_value=Response(500, text="db error")
        )

        ok, detail = post_comment_wp("42", 100, "hi")

        assert ok is False
        assert "reply failed" in detail
        assert "500" in detail
