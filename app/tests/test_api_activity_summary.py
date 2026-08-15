# pyright: reportMissingImports=false
"""Tests for `GET /api/v1/activity/summary` in `api/approval_api.py`.

The endpoint exists because the Dashboard's "today" tile used to tail 200
`/activity` rows and tally them in the browser. Hourly `trace` heartbeats
fill that window (187 of the last 200 on 2026-08-14), so real comments and
joins scrolled off the end and the tile read zero while the flows were in
fact posting. Summing server-side over the whole log removes the window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from api import approval_api
from fastapi.testclient import TestClient

from lib import activity_log

client = TestClient(approval_api.app)


def _seed_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, str]]) -> None:
    target = tmp_path / "logs" / "engagement_log.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)


def _row(date: str, action: str, platform: str = "facebook") -> dict[str, str]:
    return {
        "date": date,
        "timestamp": f"{date}T12:00:00+00:00",
        "action": action,
        "platform": platform,
    }


def test_summary_tallies_the_requested_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_log(
        monkeypatch,
        tmp_path,
        [
            _row("2026-08-14", "comment"),
            _row("2026-08-14", "like", "instagram"),
            _row("2026-08-14", "like", "instagram"),
            _row("2026-08-13", "comment"),
        ],
    )

    res = client.get("/api/v1/activity/summary", params={"date": "2026-08-14"})

    assert res.status_code == 200
    body = res.json()
    assert body["date"] == "2026-08-14"
    assert body["counts"]["comment"] == 1
    assert body["counts"]["like"] == 2
    assert body["total"] == 3


def test_summary_sees_past_a_trace_flood(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = [_row("2026-08-14", "trace", "system") for _ in range(400)]
    rows.append(_row("2026-08-14", "comment"))
    rows += [_row("2026-08-14", "trace", "system") for _ in range(400)]
    _seed_log(monkeypatch, tmp_path, rows)

    body = client.get("/api/v1/activity/summary", params={"date": "2026-08-14"}).json()

    assert body["counts"]["comment"] == 1
    assert body["total"] == 1, "trace heartbeats must not count as activity"


def test_summary_rejects_a_malformed_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_log(monkeypatch, tmp_path, [_row("2026-08-14", "comment")])

    res = client.get("/api/v1/activity/summary", params={"date": "14-08-2026"})

    assert res.status_code == 422


def test_summary_defaults_to_today_and_always_returns_every_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_log(monkeypatch, tmp_path, [_row("1999-01-01", "comment")])

    body = client.get("/api/v1/activity/summary").json()

    assert body["total"] == 0
    assert set(body["counts"]) == set(activity_log.VALID_ACTIONS)
