"""Tests for the single source of truth mapping a flow id to its
`worker_runs.worker_label` -- see lib/worker_labels.py's module docstring for
why this module exists (four scripts, three different label schemes, none of
which the frontend's status lookup could read)."""

from __future__ import annotations

from lib.worker_labels import TASK_ID_PREFIX, worker_label_for_flow


def test_worker_label_matches_the_schedule_tasks_id_convention() -> None:
    assert worker_label_for_flow("ig-scanner") == "dogfood-ig-scanner"
    assert worker_label_for_flow("fb-scanner") == "dogfood-fb-scanner"
    assert worker_label_for_flow("fb-comment") == "dogfood-fb-comment"
    assert worker_label_for_flow("ig-comment") == "dogfood-ig-comment"


def test_worker_label_uses_the_shared_prefix_constant() -> None:
    assert worker_label_for_flow("x") == f"{TASK_ID_PREFIX}x"
