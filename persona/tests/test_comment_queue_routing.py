"""Tests for per-platform comment-queue routing (IG/FB independent loops)."""

from __future__ import annotations

import pytest

from lib.comment_queue_routing import (
    guard_key_for,
    parse_platform_arg,
    queue_path_for,
)


class TestParsePlatformArg:
    def test_space_separated(self) -> None:
        assert parse_platform_arg(["prog", "--platform", "instagram"]) == "instagram"

    def test_equals_form(self) -> None:
        assert parse_platform_arg(["prog", "--platform=facebook"]) == "facebook"

    def test_absent_returns_none(self) -> None:
        assert parse_platform_arg(["prog", "--force"]) is None

    def test_case_insensitive(self) -> None:
        assert parse_platform_arg(["prog", "--platform", "Instagram"]) == "instagram"

    def test_unknown_platform_exits(self) -> None:
        with pytest.raises(SystemExit):
            parse_platform_arg(["prog", "--platform", "tiktok"])


class TestQueuePathFor:
    def test_instagram_has_own_queue(self) -> None:
        assert queue_path_for("instagram").name == "instagram_comment_queue.json"

    def test_facebook_has_own_queue(self) -> None:
        assert queue_path_for("facebook").name == "facebook_comment_queue.json"

    def test_wordpress_uses_legacy_queue(self) -> None:
        assert queue_path_for("wordpress").name == "comment_queue.json"

    def test_none_uses_legacy_queue(self) -> None:
        assert queue_path_for(None).name == "comment_queue.json"

    def test_ig_and_fb_queues_are_distinct(self) -> None:
        assert queue_path_for("instagram") != queue_path_for("facebook")


class TestGuardKeyFor:
    def test_per_platform_key(self) -> None:
        assert guard_key_for("instagram") == "comment_composer_instagram"
        assert guard_key_for("facebook") == "comment_composer_facebook"

    def test_none_keeps_legacy_key(self) -> None:
        assert guard_key_for(None) == "comment_composer"

    def test_ig_and_fb_guard_keys_differ(self) -> None:
        # The IG loop's re-run guard must never block the FB loop and vice versa.
        assert guard_key_for("instagram") != guard_key_for("facebook")
