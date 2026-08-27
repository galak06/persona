"""Tests for notifier.py — Telegram reply parsing."""

from __future__ import annotations

from lib.notifier import _parse_reply

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


class TestSendHoldsAlertsThroughAnOutage:
    """`notifier.send` parks a network-lost alert and replays it later.

    The 2026-08-26 `fb-engager` run is the case: it died on
    `ERR_NAME_NOT_RESOLVED`, then its own alert failed to resolve
    `api.telegram.org` and the failure went unreported entirely.
    """

    @staticmethod
    def _isolate(tmp_path, monkeypatch):
        from lib import notifier, notifier_outbox

        monkeypatch.setattr(
            notifier_outbox, "outbox_path", lambda: tmp_path / "notifier_outbox.jsonl"
        )
        monkeypatch.setattr(
            notifier, "_load_config", lambda: {"bot_token": "t", "chat_id": "c"}
        )
        return notifier, notifier_outbox

    def test_network_failure_parks_the_alert(self, tmp_path, monkeypatch):
        notifier, notifier_outbox = self._isolate(tmp_path, monkeypatch)

        def boom(*_a, **_k):
            raise OSError("Failed to resolve 'api.telegram.org'")

        monkeypatch.setattr(notifier.requests, "post", boom)
        assert notifier.send("fb-engager failed") is False
        assert notifier_outbox.pending_count() == 1

    def test_next_success_replays_what_was_held(self, tmp_path, monkeypatch):
        notifier, notifier_outbox = self._isolate(tmp_path, monkeypatch)
        notifier_outbox.enqueue("held alert")

        sent: list[str] = []

        class _Resp:
            ok = True
            status_code = 200

        def ok(_url, json=None, timeout=None):
            sent.append(json["text"])
            return _Resp()

        monkeypatch.setattr(notifier.requests, "post", ok)
        assert notifier.send("live alert") is True
        assert sent == ["live alert", "held alert"]
        assert notifier_outbox.pending_count() == 0

    def test_telegram_rejection_is_not_parked(self, tmp_path, monkeypatch):
        """A 4xx repeats identically on replay -- parking it would spam the
        outbox forever with something that can never send."""
        notifier, notifier_outbox = self._isolate(tmp_path, monkeypatch)

        class _Resp:
            ok = False
            status_code = 400
            text = "Bad Request: chat not found"

        monkeypatch.setattr(notifier.requests, "post", lambda *_a, **_k: _Resp())
        assert notifier.send("doomed") is False
        assert notifier_outbox.pending_count() == 0

    def test_telegram_5xx_is_parked(self, tmp_path, monkeypatch):
        notifier, notifier_outbox = self._isolate(tmp_path, monkeypatch)

        class _Resp:
            ok = False
            status_code = 503
            text = "Service Unavailable"

        monkeypatch.setattr(notifier.requests, "post", lambda *_a, **_k: _Resp())
        assert notifier.send("transient") is False
        assert notifier_outbox.pending_count() == 1
