"""Tests for rate_limiter.py — daily limits and action tracking."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import rate_limiter


@pytest.fixture
def tmp_state(tmp_path):
    """Redirect rate limiter state to a temp file."""
    state_file = tmp_path / "rate_limit_tracker.json"
    state_file.write_text("{}")
    with patch.object(rate_limiter, "STATE_FILE", state_file):
        yield state_file


class TestCanAct:
    def test_fresh_state_allows_action(self, tmp_state):
        assert rate_limiter.can_act("facebook", "comment") is True

    def test_at_limit_blocks_action(self, tmp_state):
        today = date.today().isoformat()
        state = {today: {"facebook:comment": 5}}
        tmp_state.write_text(json.dumps(state))
        assert rate_limiter.can_act("facebook", "comment") is False

    def test_below_limit_allows_action(self, tmp_state):
        today = date.today().isoformat()
        state = {today: {"facebook:comment": 4}}
        tmp_state.write_text(json.dumps(state))
        assert rate_limiter.can_act("facebook", "comment") is True

    def test_different_day_resets(self, tmp_state):
        state = {"2020-01-01": {"facebook:comment": 5}}
        tmp_state.write_text(json.dumps(state))
        assert rate_limiter.can_act("facebook", "comment") is True

    def test_unknown_action_raises(self, tmp_state):
        with pytest.raises(ValueError, match="Unknown action key"):
            rate_limiter.can_act("facebook", "nonexistent")

    def test_all_platforms_start_allowed(self, tmp_state):
        assert rate_limiter.can_act("facebook", "comment") is True
        assert rate_limiter.can_act("facebook", "group_visit") is True
        assert rate_limiter.can_act("instagram", "like") is True
        assert rate_limiter.can_act("instagram", "comment") is True


class TestRecordAction:
    def test_increments_count(self, tmp_state):
        count = rate_limiter.record_action("facebook", "comment")
        assert count == 1
        count = rate_limiter.record_action("facebook", "comment")
        assert count == 2

    def test_persists_to_file(self, tmp_state):
        rate_limiter.record_action("instagram", "like")
        state = json.loads(tmp_state.read_text())
        today = date.today().isoformat()
        assert state[today]["instagram:like"] == 1

    def test_raises_at_limit(self, tmp_state):
        for _ in range(5):
            rate_limiter.record_action("facebook", "comment")
        with pytest.raises(RuntimeError, match="Daily limit reached"):
            rate_limiter.record_action("facebook", "comment")

    def test_different_actions_independent(self, tmp_state):
        rate_limiter.record_action("facebook", "comment")
        rate_limiter.record_action("facebook", "group_visit")
        state = json.loads(tmp_state.read_text())
        today = date.today().isoformat()
        assert state[today]["facebook:comment"] == 1
        assert state[today]["facebook:group_visit"] == 1


class TestGetDailyStatus:
    def test_empty_state_all_zero(self, tmp_state):
        status = rate_limiter.get_daily_status()
        for _key, info in status.items():
            assert info["used"] == 0
            assert info["remaining"] == info["limit"]

    def test_reflects_recorded_actions(self, tmp_state):
        rate_limiter.record_action("instagram", "like")
        rate_limiter.record_action("instagram", "like")
        status = rate_limiter.get_daily_status()
        assert status["instagram:like"]["used"] == 2
        assert status["instagram:like"]["remaining"] == 6


class TestDailyLimits:
    def test_limits_match_spec(self):
        # Canonical daily caps emitted by tools.profiles_build from
        # profiles/*.json. Keys use the flat <platform>:<action> form;
        # legacy "ig_*" prefix dropped in slice A.
        assert rate_limiter.DAILY_LIMITS["facebook:comment"] == 5
        assert rate_limiter.DAILY_LIMITS["facebook:like"] == 5
        assert rate_limiter.DAILY_LIMITS["facebook:group_visit"] == 6
        assert rate_limiter.DAILY_LIMITS["facebook:group_post"] == 10
        assert rate_limiter.DAILY_LIMITS["facebook:group_join"] == 5
        assert rate_limiter.DAILY_LIMITS["facebook:page_post"] == 3
        assert rate_limiter.DAILY_LIMITS["instagram:like"] == 8
        assert rate_limiter.DAILY_LIMITS["instagram:comment"] == 10
        assert rate_limiter.DAILY_LIMITS["instagram:follow"] == 22
        assert rate_limiter.DAILY_LIMITS["instagram:feed_post"] == 2
        assert rate_limiter.DAILY_LIMITS["wordpress:reply"] == 20
        # Legacy bucket preserved in rate_limiter (no profile field yet).
        assert rate_limiter.DAILY_LIMITS["instagram:own_reply"] == 15

    def test_delay_ranges_are_int_tuples(self):
        # Delays are parsed from strings like "30-120s random" into
        # (lo, hi) integer-second tuples by rate_limiter._parse_delay.
        assert rate_limiter.DELAY_RANGES["facebook:comment"] == (30, 120)
        assert rate_limiter.DELAY_RANGES["facebook:group_visit"] == (45, 180)
        assert rate_limiter.DELAY_RANGES["facebook:like"] == (30, 90)
        assert rate_limiter.DELAY_RANGES["instagram:like"] == (10, 45)
        assert rate_limiter.DELAY_RANGES["instagram:comment"] == (120, 180)
        assert rate_limiter.DELAY_RANGES["instagram:follow"] == (60, 180)
        assert rate_limiter.DELAY_RANGES["wordpress:reply"] == (15, 45)
