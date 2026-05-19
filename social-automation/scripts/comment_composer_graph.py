"""
comment_composer_graph.py — LangGraph alternative to comment_approver + comment_poster.

Same per-item lifecycle as the legacy two-script flow, compiled as one graph.
Reuses the same validators, posters, and Telegram approval — no behavior
changes to those.

LangGraph features in use:
    - StateGraph with conditional edges + retry loop
    - SqliteSaver checkpointer (resume crashed batches; one thread per queue item)
    - Streaming with stream_mode=["updates","values"] for per-node visibility
    - interrupt() / Command(resume=...) for cross-run human-in-loop approval —
      no 12h blocking wait, no re-drafting on retry
    - OpenInference / Phoenix tracing (opt-in via PHOENIX_ENABLED=true) — sends
      OTel spans to a local Phoenix container at PHOENIX_ENDPOINT for browseable
      run history. See docker/phoenix/docker-compose.yml.

Usage:
    python scripts/comment_composer_graph.py            # full pipeline
    python scripts/comment_composer_graph.py --dry-run  # log decisions, no writes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from local_env import load_local_env

# Load env early so PHOENIX_ENABLED etc. are visible before tracing setup.
load_local_env()

from langgraph.types import Command

from comment_graph import Context, build_graph
from lib.engagement import posted_targets
from notifier import (
    poll_for_reply,
    send_approval_request,
    skill_finished,
    skill_started,
)

QUEUE_FILE = settings.paths.comment_queue
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"
SESSION_FB = settings.paths.facebook_session
SESSION_IG = settings.paths.instagram_session
CHECKPOINT_DB = PROJECT_ROOT / ".claude/state/comment_graph_checkpoints.db"
PENDING_APPROVALS_FILE = PROJECT_ROOT / ".claude/state/comment_graph_pending_approvals.json"

# Bound the in-run approval wait so cron doesn't block forever. Items still
# unanswered when this elapses are persisted to PENDING_APPROVALS_FILE and
# resumed on the next run.
APPROVAL_POLL_SECONDS_FRESH = 180
APPROVAL_POLL_SECONDS_RESUME = 5


# Engagement history reconstruction now lives in lib.engagement.posted_targets,
# which applies the canonical filter `actions={"comment", "like"}` (drift fix:
# this script previously counted any logged action — including group_post —
# which silently suppressed approval prompts for first conversational comments
# to publishing-only groups).


def _open_browsers(ctx: Context) -> None:
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    if SESSION_FB.exists():
        ctx.fb_page = browser.new_context(storage_state=str(SESSION_FB)).new_page()
    if SESSION_IG.exists():
        ctx.ig_page = browser.new_context(storage_state=str(SESSION_IG)).new_page()


def _summarize_delta(delta: dict | None) -> str:
    """One-line summary of a node's state delta for streaming output."""
    if not delta:
        return "no-op"
    parts = []
    if "decision" in delta:
        parts.append(f"decision={delta['decision']}")
    if delta.get("draft"):
        parts.append(f"draft={len(delta['draft'])}c")
    if "violations" in delta:
        v = delta["violations"]
        parts.append(f"violations={len(v)}")
    if "retries" in delta:
        parts.append(f"retries={delta['retries']}")
    if "requires_approval" in delta:
        parts.append(f"approval={delta['requires_approval']}")
    if "error" in delta and delta.get("error"):
        parts.append(f"err={str(delta['error'])[:40]}")
    return " ".join(parts) or "ok"


def _setup_phoenix_tracing() -> bool:
    """Enable OpenInference tracing → local Phoenix container if PHOENIX_ENABLED.

    Idempotent: returns True if tracing was activated, False if disabled or
    setup failed. Failures are non-fatal — the graph still runs without traces.
    """
    if os.environ.get("PHOENIX_ENABLED", "").lower() not in ("true", "1", "yes"):
        return False
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
        project = os.environ.get("PHOENIX_PROJECT_NAME", "comment-composer")
        tracer_provider = register(project_name=project, endpoint=endpoint, auto_instrument=False)
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider, skip_dep_check=True)
        print(f"[phoenix] tracing → {endpoint} (project: {project})")
        return True
    except Exception as e:
        print(f"[phoenix] tracing setup failed: {e} — continuing without traces")
        return False


def _load_pending_approvals() -> dict:
    if not PENDING_APPROVALS_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_APPROVALS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_pending_approvals(data: dict) -> None:
    PENDING_APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_APPROVALS_FILE.write_text(json.dumps(data, indent=2))


def _extract_interrupt(stream_event_payload) -> dict | None:
    """Return the interrupt's payload value if `__interrupt__` was emitted, else None."""
    if not isinstance(stream_event_payload, dict):
        return None
    intr = stream_event_payload.get("__interrupt__")
    if not intr:
        return None
    # `__interrupt__` is a tuple of Interrupt objects in stream updates.
    first = intr[0] if isinstance(intr, (list, tuple)) else intr
    return getattr(first, "value", first if isinstance(first, dict) else None)


def _stream_graph(graph, stream_input, cfg) -> tuple[dict, dict | None]:
    """Drive a graph step (fresh or Command(resume=...)). Returns (final_state, interrupt_payload).

    `interrupt_payload` is non-None iff the graph paused at await_approval.
    `subgraphs=True` makes child-graph node events bubble up so per-node
    visibility survives the subgraph refactor (events come back as
    ((namespace,...), mode, payload) when inside a subgraph).
    """
    final_state: dict = {}
    interrupt_payload: dict | None = None
    for event in graph.stream(
        stream_input, config=cfg, stream_mode=["updates", "values"], subgraphs=True
    ):
        # subgraphs=True yields (ns, mode, payload). ns=() at parent level.
        ns, mode, payload = event
        prefix = f"[{ns[-1].split(':')[0]}] " if ns else ""
        if mode == "updates":
            for node_name, delta in payload.items():
                if node_name == "__interrupt__":
                    interrupt_payload = _extract_interrupt(payload)
                    print("  · __interrupt__: paused awaiting approval")
                else:
                    print(f"  · {prefix}{node_name}: {_summarize_delta(delta)}")
        else:  # values
            if not ns:
                final_state = payload
    return final_state, interrupt_payload


def _apply_decision(item: dict, result: dict) -> str:
    decision = result.get("decision", "no_draft")
    if decision == "posted":
        item["status"] = "posted"
        item["comment_text"] = result.get("draft", "")
        item["posted_at"] = datetime.now(UTC).isoformat()
    elif decision == "approval_pending":
        pass  # leave status="pending" so next run retries
    elif decision == "post_failed":
        item["status"] = "POST_FAILED"
        item["error"] = result.get("error")
    else:
        item["status"] = decision.upper()
    return decision


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run", action="store_true", help="log decisions, skip approval+post+queue write"
    )
    args = ap.parse_args()

    _setup_phoenix_tracing()

    queue = json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []
    pending = [q for q in queue if q.get("status") == "pending"]
    if not pending:
        print("no pending items")
        return

    ctx = Context(previously_posted=posted_targets(), dry_run=args.dry_run)
    if not args.dry_run:
        _open_browsers(ctx)

    skill_started("comment-composer-graph", f"{len(pending)} items")

    # Checkpointer: dry-run uses in-memory, real runs persist per-thread state to
    # SQLite so a crash mid-batch resumes from the last completed node on next run.
    if args.dry_run:
        from langgraph.checkpoint.memory import MemorySaver

        cp_ctx = nullcontext(MemorySaver())
    else:
        CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        from langgraph.checkpoint.sqlite import SqliteSaver

        cp_ctx = SqliteSaver.from_conn_string(str(CHECKPOINT_DB))

    counts: dict[str, int] = {}
    pending_approvals = {} if args.dry_run else _load_pending_approvals()

    with cp_ctx as checkpointer:
        graph = build_graph(checkpointer=checkpointer)

        # Pass 1: resume any threads paused at await_approval from a previous run.
        # Quick poll only — if no reply, leave them paused.
        if not args.dry_run and pending_approvals:
            for thread_id, rec in list(pending_approvals.items()):
                cfg = {"configurable": {"context": ctx, "thread_id": thread_id}}
                reply = poll_for_reply(
                    rec["offset"], rec["draft"], max_seconds=APPROVAL_POLL_SECONDS_RESUME
                )
                if not reply:
                    print(f"  [{thread_id}] no reply yet — staying paused")
                    counts["approval_pending"] = counts.get("approval_pending", 0) + 1
                    continue
                print(f"  [{thread_id}] reply received: {reply['action']} — resuming")
                final_state, _ = _stream_graph(graph, Command(resume=reply), cfg)
                # Find the queue item for this thread and apply
                pid = thread_id.removeprefix("queue-")
                qitem = next((q for q in queue if q.get("post_id") == pid), None)
                if qitem is not None:
                    decision = _apply_decision(qitem, final_state)
                    counts[decision] = counts.get(decision, 0) + 1
                    target = qitem.get("group_name") or qitem.get("hashtag", "?")
                    print(f"  [{decision}] {target}\n")
                pending_approvals.pop(thread_id, None)
                _save_pending_approvals(pending_approvals)
                QUEUE_FILE.write_text(json.dumps(queue, indent=2))

        # Pass 2: process fresh pending items (skip ones already paused awaiting approval).
        for item in pending:
            thread_id = f"queue-{item['post_id']}"
            if thread_id in pending_approvals:
                continue  # handled by Pass 1
            cfg = {"configurable": {"context": ctx, "thread_id": thread_id}}

            final_state, interrupt_payload = _stream_graph(graph, {"queue_item": item}, cfg)

            # If the graph paused at await_approval, send Telegram + poll briefly.
            if interrupt_payload is not None:
                send_result = send_approval_request(
                    platform=interrupt_payload["platform"],
                    group_or_hashtag=interrupt_payload["group_or_hashtag"],
                    post_preview=interrupt_payload["post_preview"],
                    draft_comment=interrupt_payload["draft_comment"],
                    relevance_score=interrupt_payload["relevance_score"],
                )
                if not send_result.get("sent"):
                    print(
                        f"  [{thread_id}] approval send failed ({send_result.get('reason')}) — keeping pending"
                    )
                    counts["approval_pending"] = counts.get("approval_pending", 0) + 1
                    continue

                reply = poll_for_reply(
                    send_result["offset"],
                    interrupt_payload["draft_comment"],
                    max_seconds=APPROVAL_POLL_SECONDS_FRESH,
                )
                if reply:
                    final_state, _ = _stream_graph(graph, Command(resume=reply), cfg)
                else:
                    # Persist for next run's Pass 1 to resume.
                    pending_approvals[thread_id] = {
                        "offset": send_result["offset"],
                        "draft": interrupt_payload["draft_comment"],
                        "sent_at": datetime.now(UTC).isoformat(),
                    }
                    if not args.dry_run:
                        _save_pending_approvals(pending_approvals)
                    print(
                        f"  [{thread_id}] no reply within {APPROVAL_POLL_SECONDS_FRESH}s — paused for next run"
                    )
                    counts["approval_pending"] = counts.get("approval_pending", 0) + 1
                    continue

            decision = _apply_decision(item, final_state)
            counts[decision] = counts.get(decision, 0) + 1
            target = item.get("group_name") or item.get("hashtag", "?")
            print(f"  [{decision}] {target}\n")
            if not args.dry_run:
                QUEUE_FILE.write_text(json.dumps(queue, indent=2))

    summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"=== done === {summary}")
    skill_finished("comment-composer-graph", summary)


if __name__ == "__main__":
    main()
