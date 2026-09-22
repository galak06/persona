"""The one place a flow id becomes its `worker_runs.worker_label`.

Every flow has exactly one correct label: the `schedule_tasks.id` this brand's
row for that flow carries -- that's the string `api/approval_api.py`'s
`/workers` endpoint and its trigger guard key their lookups on. A worker
script that writes its own run status under a DIFFERENT string writes to a row
nothing ever reads: the frontend Schedule page shows a stuck status forever,
and the trigger endpoint's "already ran today" guard can't see real
completions either.

The label is `<brand_id>-<flow_id>`, matching what
`lib.brand_provisioning._flow_to_task` writes. This module used to hardcode
`"dogfood-"` instead, which disagreed with provisioning and had two
consequences. Re-provisioning `dogfoodandfun` created a SECOND row per flow
(`dogfoodandfun-ig-engager` beside `dogfood-ig-engager`), so the dispatcher
opened a run under one id while the script closed the other and the UI's row
stayed `running` forever. And any second brand would have had every engager
run reported under one brand's hardcoded prefix -- the multi-brand story was
broken, unnoticed only because there is one brand.

`brand_id` resolves from `$BRAND_DIR`'s folder name when not passed, which is
what the worker sets for every flow subprocess. `LEGACY_TASK_ID_PREFIX` is
kept solely so the id migration can recognise pre-migration rows.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The prefix task ids used before they were brand-derived. Retained for
#: recognising un-migrated rows; never build a new label from it.
LEGACY_TASK_ID_PREFIX = "dogfood-"


def current_brand_id() -> str:
    """This process's brand id -- `$BRAND_DIR`'s folder name.

    Empty when `BRAND_DIR` is unset, which callers treat as "cannot build a
    label": better a caller that fails visibly than one that writes a run
    status under a guessed brand and desyncs another brand's row.
    """
    brand_dir = os.environ.get("BRAND_DIR", "").strip()
    return Path(brand_dir).name if brand_dir else ""


def worker_label_for_flow(flow_id: str, brand_id: str | None = None) -> str:
    """The `schedule_tasks.id` -- and so `worker_runs.worker_label` -- for a flow.

    `"ig-engager"` -> `"<brand>-ig-engager"`. Raises when no brand can be
    resolved: a label built without one is the exact defect this module
    exists to prevent, and silently returning a bare flow id would recreate it.
    """
    brand = (brand_id or current_brand_id()).strip()
    if not brand:
        raise RuntimeError(
            f"cannot build a worker label for {flow_id!r}: no brand_id given and "
            "BRAND_DIR is unset"
        )
    return f"{brand}-{flow_id}"


def flow_id_from_task_id(task_id: str, brand_id: str | None = None) -> str:
    """The flow id inside a task id, or `""` when it carries no known prefix.

    Accepts the legacy `dogfood-` form as well as `<brand>-`, so a worker
    still recognises a row written before the migration -- stripping only the
    current prefix would make un-migrated rows look prefix-less and lose their
    per-flow log file.
    """
    brand = (brand_id or current_brand_id()).strip()
    for prefix in ([f"{brand}-"] if brand else []) + [LEGACY_TASK_ID_PREFIX]:
        if task_id.startswith(prefix):
            return task_id[len(prefix) :]
    return ""
