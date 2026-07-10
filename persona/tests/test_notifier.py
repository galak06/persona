"""Tests for notifier.py — Telegram reply parsing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from notifier import _parse_reply

DRAFT = "We tried this with Nalla and it worked great. What protein do you use?"


class TestParseReply:
    """Tests for Telegram approval reply parsing."""

    def test_yes_approves(self):
        for word in ["yes", "y", "approve", "ok", "post it", "post", "Yes", "YES"]:
            result = _parse_reply(word, DRAFT)
            assert result["action"] == "approved", f"'{word}' should approve"
            assert result["comment"] == DRAFT

    def test_skip_skips(self):
        for word in ["skip", "s", "no", "n", "nope", "Skip", "NO"]:
            result = _parse_reply(word, DRAFT)
            assert result["action"] == "skipped", f"'{word}' should skip"

    def test_edit_with_prefix(self):
        result = _parse_reply("edit: Nalla loved this — what brand did you try?", DRAFT)
        assert result["action"] == "edited"
        assert result["comment"] == "Nalla loved this — what brand did you try?"

    def test_edit_prefix_case_insensitive(self):
        result = _parse_reply("Edit: new comment text here?", DRAFT)
        assert result["action"] == "edited"
        assert result["comment"] == "new comment text here?"

    def test_edit_empty_text_skips(self):
        result = _parse_reply("edit:", DRAFT)
        assert result["action"] == "skipped"

    def test_long_text_with_question_treated_as_edit(self):
        new_text = "We actually tried something different with Nalla last month and it was amazing?"
        result = _parse_reply(new_text, DRAFT)
        assert result["action"] == "edited"
        assert result["comment"] == new_text

    def test_short_unknown_text_skips(self):
        result = _parse_reply("hmm", DRAFT)
        assert result["action"] == "skipped"

    def test_whitespace_handling(self):
        result = _parse_reply("  yes  ", DRAFT)
        assert result["action"] == "approved"

    def test_post_keyword(self):
        result = _parse_reply("post", DRAFT)
        assert result["action"] == "approved"
