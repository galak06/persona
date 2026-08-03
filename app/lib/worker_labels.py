"""The one place a flow id becomes its `worker_runs.worker_label`.

Every flow has exactly one correct label: the `schedule_tasks.id` this brand's
row for that flow carries (e.g. `dogfood-ig-scanner`) -- that's the string
`api/approval_api.py`'s `/workers` endpoint and its trigger guard key their
lookups on. A worker script that writes its own run status under a
DIFFERENT string (as `ig_scan.py`/`fb_scan.py` built dynamically from
`brand_dir.name`, or `ig_comment.py`'s unrelated hardcoded literal, both did
before this module existed) writes to a row nothing ever reads: the frontend
Schedule page shows a stale/stuck status forever, and the trigger endpoint's
"already ran today" guard can't see real completions either.

`TASK_ID_PREFIX` was already duplicated, hardcoded, in `api/schedule_config.py`
and `scripts/regenerate_plists.py`; this module is the single definition both
now import, plus the four worker scripts that need to build their own label.
"""

from __future__ import annotations

TASK_ID_PREFIX = "dogfood-"


def worker_label_for_flow(flow_id: str) -> str:
    """The `schedule_tasks.id` -- and therefore `worker_runs.worker_label` --
    for `flow_id` (e.g. `"ig-scanner"` -> `"dogfood-ig-scanner"`)."""
    return f"{TASK_ID_PREFIX}{flow_id}"
