"""Best-effort Langfuse tracing for the live Gemini calls in lib.reply_drafter.

`lib.reply_drafter._call_gemini`/`_call_gemini_json` are the only LLM calls in
the active pipeline (the LangGraph/Anthropic path in `comment_graph.py` is
inactive and traced separately via Phoenix — untouched here). Wrapping them
gives visibility into prompts, completions, and the agent's engage/decline
reasoning in Langfuse's UI, complementing the structured JSONL/Grafana logs
which don't carry full prompt/response text.

Env: LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_BASE_URL. With either
key missing, or any error talking to Langfuse, `trace_llm_call` degrades to
calling `call()` directly — tracing is observability, never allowed to break
or alter a live drafting call. `call()`'s own return value and exceptions
always propagate untouched.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _client() -> Any | None:
    if not os.environ.get("LANGFUSE_SECRET_KEY") or not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception as e:
        logger.warning("langfuse: client init failed: %s", e)
        return None


def trace_llm_call(name: str, *, model: str, input_text: str, call: Callable[[], T]) -> T:
    """Run `call()`, best-effort wrapped in a Langfuse "generation" trace.

    `call()` is invoked exactly once, outside any tracing try/except, so its
    return value and exceptions are never affected by Langfuse being
    unconfigured, unreachable, or erroring.
    """
    client = _client()
    if client is None:
        return call()

    try:
        cm = client.start_as_current_observation(
            as_type="generation", name=name, model=model, input=input_text
        )
        generation = cm.__enter__()
    except Exception as e:
        logger.warning("langfuse: failed to start generation %r: %s", name, e)
        return call()

    try:
        result = call()
    except BaseException as exc:
        try:
            cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as e:
            logger.warning("langfuse: failed to close generation %r after error: %s", name, e)
        raise
    else:
        try:
            generation.update(output=result)
        except Exception as e:
            logger.warning("langfuse: failed to record output for %r: %s", name, e)
        try:
            cm.__exit__(None, None, None)
        except Exception as e:
            logger.warning("langfuse: failed to close generation %r: %s", name, e)
        return result
