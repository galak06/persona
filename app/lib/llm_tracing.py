"""Best-effort Langfuse tracing for the live Gemini calls in lib.reply_drafter.

`lib.gemini_client._call_gemini`/`call_json` — reached through
`lib.llm_client.GeminiLLM` — are the only traced LLM calls in the
active pipeline (the LangGraph/Anthropic path in `comment_graph.py` is
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
from collections.abc import Awaitable, Callable
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


def trace_llm_call(
    name: str, *, model: str, input_text: str | dict[str, str], call: Callable[[], T]
) -> T:
    """Run `call()`, best-effort wrapped in a Langfuse "generation" trace.

    `input_text` is either the plain prompt string or a small JSON-serializable
    dict (e.g. `{"system": ..., "user": ...}` for system/user-split calls) —
    Langfuse accepts both as `input`.

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


async def atrace_llm_call(
    name: str, *, model: str, input_text: str, call: Callable[[], Awaitable[T]]
) -> T:
    """Async twin of `trace_llm_call` for coroutine-based LLM transports.

    Langfuse's observation context manager is synchronous, but entering and
    exiting it around an `await` is safe: the span is opened on this task,
    the coroutine runs, and the span is closed on the same task. Nothing in
    between touches the client.

    Same contract as the sync version — `call()` is awaited exactly once,
    outside any tracing try/except, so its result and exceptions are never
    affected by Langfuse being unconfigured, unreachable, or erroring.
    """
    client = _client()
    if client is None:
        return await call()

    try:
        cm = client.start_as_current_observation(
            as_type="generation", name=name, model=model, input=input_text
        )
        generation = cm.__enter__()
    except Exception as e:
        logger.warning("langfuse: failed to start generation %r: %s", name, e)
        return await call()

    try:
        result = await call()
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
