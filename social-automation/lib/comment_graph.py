"""
LangGraph definition for the comment-composer pipeline.

One graph instance handles a single queue item end-to-end:
    preflight → draft → validate → (regenerate ↺ ≤2x) → decide_approval
              → await_approval → post → record

Wraps existing helpers from comment_generator, deduplication, notifier,
rate_limiter, and scripts/comment_poster — no behavior changes to those.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from comment_generator import (
    build_claude_prompt,
    draft_comment_from_template,
    validate_voice,
)
from comment_poster import (
    log_engagement,
    post_comment_fb,
    post_comment_ig,
    post_comment_wp,
)
from deduplication import is_duplicate, mark_engaged
from rate_limiter import can_act, record_action, wait_random_delay

MAX_RETRIES = 2

Decision = Literal[
    "posted",
    "skipped_duplicate",
    "rate_limited",
    "validation_failed",
    "user_skipped",
    "approval_pending",
    "post_failed",
    "no_draft",
]


class State(TypedDict, total=False):
    queue_item: dict[str, Any]
    draft: str | None
    violations: list[str]
    retries: int
    requires_approval: bool
    decision: Decision
    error: str | None


@dataclass
class Context:
    """Carried via RunnableConfig.configurable — not part of checkpointed state."""

    fb_page: Any | None = None
    ig_page: Any | None = None
    previously_posted: set[str] = field(default_factory=set)
    dry_run: bool = False


def n_preflight(state: State, config) -> dict:
    item = state["queue_item"]
    platform = item["platform"]
    action = "comment"
    if not can_act(platform, action):
        return {"decision": "rate_limited"}
    if is_duplicate(platform, item["post_id"]):
        return {"decision": "skipped_duplicate"}
    return {"retries": 0, "violations": []}


def n_draft(state: State, config) -> dict:
    item = state["queue_item"]
    if existing := item.get("draft_comment"):
        return {"draft": existing}
    draft = draft_comment_from_template(
        item["category"], item["post_text"], item.get("post_author", "")
    )
    if draft:
        return {"draft": draft}
    prompt = build_claude_prompt(
        item["post_text"],
        item["category"],
        item.get("group_name") or item.get("hashtag", ""),
    )
    return {"draft": _claude_draft(prompt)}


def n_validate(state: State, config) -> dict:
    draft = state.get("draft")
    if not draft:
        return {"violations": ["no draft produced"]}
    valid, violations = validate_voice(draft)
    return {"violations": [] if valid else violations}


def n_regenerate(state: State, config) -> dict:
    item = state["queue_item"]
    feedback = "\n".join(f"- {v}" for v in state["violations"])
    prompt = (
        build_claude_prompt(
            item["post_text"],
            item["category"],
            item.get("group_name") or item.get("hashtag", ""),
        )
        + f"\n\n## Previous attempt failed validation\nFix:\n{feedback}\n\nRewrite."
    )
    return {"draft": _claude_draft(prompt), "retries": state["retries"] + 1}


def n_decide_approval(state: State, config) -> dict:
    ctx: Context = config["configurable"]["context"]
    item = state["queue_item"]
    draft = state["draft"] or ""
    group = item.get("group_name") or item.get("hashtag") or item.get("parent_post_title", "")
    needs = (
        item.get("requires_approval", False)
        or item["platform"] in ("instagram", "wordpress")
        or "dogfoodandfun.com" in draft.lower()
        or group not in ctx.previously_posted
    )
    return {"requires_approval": needs}


def n_await_approval(state: State, config) -> dict:
    """Pause the graph here — the runner sends Telegram + harvests the reply.

    `interrupt(payload)` checkpoints the thread and raises a GraphInterrupt the
    first time it executes; on `Command(resume=value)`, it returns `value`. This
    lets a paused approval survive across cron runs without re-drafting.
    """
    ctx: Context = config["configurable"]["context"]
    if ctx.dry_run:
        return {}
    item = state["queue_item"]
    user_reply = interrupt(
        {
            "type": "approval_request",
            "platform": item["platform"],
            "group_or_hashtag": item.get("group_name") or item.get("hashtag", ""),
            "post_preview": item["post_text"][:200],
            "draft_comment": state.get("draft", ""),
            "relevance_score": item.get("relevance_score", 0.0),
        }
    )
    # Only reached after Command(resume=user_reply).
    action = (user_reply or {}).get("action", "skipped")
    if action in ("approved", "edited"):
        return {"draft": user_reply.get("comment", state.get("draft", ""))}
    return {"decision": "user_skipped"}


def n_post(state: State, config) -> dict:
    ctx: Context = config["configurable"]["context"]
    if ctx.dry_run:
        return {"decision": "posted"}
    item = state["queue_item"]
    platform = item["platform"]
    draft = state["draft"]
    try:
        if platform == "facebook":
            ok = post_comment_fb(ctx.fb_page, item["post_url"], draft)
        elif platform == "instagram":
            ok = post_comment_ig(ctx.ig_page, item["post_url"], draft)
        elif platform == "wordpress":
            ok, _ = post_comment_wp(item["comment_id"], item["parent_post_id"], draft)
        else:
            return {"decision": "post_failed", "error": f"unknown platform {platform}"}
    except Exception as e:
        return {"decision": "post_failed", "error": str(e)}
    return {"decision": "posted" if ok else "post_failed"}


def n_record(state: State, config) -> dict:
    ctx: Context = config["configurable"]["context"]
    if ctx.dry_run or state.get("decision") != "posted":
        return {}
    item = state["queue_item"]
    platform = item["platform"]
    action = "comment"
    record_action(platform, action)
    target = item.get("group_name") or item.get("hashtag", "")
    mark_engaged(platform, item["post_id"], "comment", target)
    log_engagement("comment", platform, target, state["draft"])
    wait_random_delay(platform, action)
    return {}


def n_validation_failed(state: State, config) -> dict:
    return {"decision": "validation_failed"}


def r_after_preflight(state: State) -> str:
    return END if state.get("decision") else "draft"


def r_after_validate(state: State) -> str:
    if not state["violations"]:
        return "decide_approval"
    return "validation_failed" if state["retries"] >= MAX_RETRIES else "regenerate"


def r_after_decide(state: State) -> str:
    return "await_approval" if state["requires_approval"] else "post"


def r_after_approval(state: State) -> str:
    return END if state.get("decision") else "post"


def _exit_if_decision_else(next_node: str):
    """Subgraphs that may set state['decision'] use this at their exit boundary."""

    def _route(state: State) -> str:
        return END if state.get("decision") else next_node

    return _route


def build_draft_subgraph():
    """preflight → draft → validate → (regenerate ↺ ≤2x) → END.

    Exit states:
      - state['decision'] in {rate_limited, skipped_duplicate, validation_failed}: terminal
      - else: state['draft'] is set + state['violations'] is empty → success exit
    """
    g = StateGraph(State)
    g.add_node("preflight", n_preflight)
    g.add_node("draft", n_draft)
    g.add_node("validate", n_validate)
    g.add_node("regenerate", n_regenerate)
    g.add_node("validation_failed", n_validation_failed)
    g.set_entry_point("preflight")
    g.add_conditional_edges("preflight", r_after_preflight, {END: END, "draft": "draft"})
    g.add_edge("draft", "validate")
    g.add_conditional_edges(
        "validate",
        r_after_validate,
        {
            "decide_approval": END,  # success: leave subgraph
            "regenerate": "regenerate",
            "validation_failed": "validation_failed",
        },
    )
    g.add_edge("regenerate", "validate")
    g.add_edge("validation_failed", END)
    return g.compile()


def build_approval_subgraph():
    """decide_approval → await_approval (interrupt) → END.

    Exit states:
      - state['decision'] == 'user_skipped': user said no
      - state['decision'] == 'approval_pending': Telegram unreachable
      - else: state['draft'] is final (possibly edited) → ready to post
    """
    g = StateGraph(State)
    g.add_node("decide_approval", n_decide_approval)
    g.add_node("await_approval", n_await_approval)
    g.set_entry_point("decide_approval")
    g.add_conditional_edges(
        "decide_approval",
        r_after_decide,
        {
            "await_approval": "await_approval",
            "post": END,  # auto-approved: leave subgraph
        },
    )
    g.add_conditional_edges("await_approval", r_after_approval, {END: END, "post": END})
    return g.compile()


def build_post_subgraph():
    """post → record → END."""
    g = StateGraph(State)
    g.add_node("post", n_post)
    g.add_node("record", n_record)
    g.set_entry_point("post")
    g.add_edge("post", "record")
    g.add_edge("record", END)
    return g.compile()


def build_graph(checkpointer=None):
    """Top-level pipeline composed of three subgraphs.

    Subgraphs are added as nodes; conditional edges route on state['decision']
    so a terminal verdict in any phase short-circuits the rest. Parent's
    checkpointer transparently persists subgraph state — interrupt() in the
    approval subgraph still pauses the whole pipeline.
    """
    g = StateGraph(State)
    g.add_node("draft_phase", build_draft_subgraph())
    g.add_node("approval_phase", build_approval_subgraph())
    g.add_node("post_phase", build_post_subgraph())

    g.set_entry_point("draft_phase")
    g.add_conditional_edges(
        "draft_phase",
        _exit_if_decision_else("approval_phase"),
        {END: END, "approval_phase": "approval_phase"},
    )
    g.add_conditional_edges(
        "approval_phase",
        _exit_if_decision_else("post_phase"),
        {END: END, "post_phase": "post_phase"},
    )
    g.add_edge("post_phase", END)
    return g.compile(checkpointer=checkpointer)


def _claude_draft(prompt: str) -> str | None:
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    client = Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()
