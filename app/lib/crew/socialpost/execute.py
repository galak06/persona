"""Real `Crew(...).kickoff()` wrapper for the Social Post Writer stage.

Same shape as `lib.crew.reels.execute.execute_reels_crew`: the only function
in this package that makes a real LLM/network call, returning `None` (logged,
not raised) on any failure so the orchestrator never crashes the run.

Retries with feedback when the parsed plan violates the hard caption rules
(`lib.crew.socialpost.rules`) -- same shape as the reels crew's beat-count
retry, because these are structural requirements (no URLs, hashtag counts,
comment-keyword CTA), not nice-to-haves.

Two things learned from live runs, both encoded below:

1. **Retries must be surgical, not a re-draft.** Asking for a fresh plan made
   the model fix the named rule and break a different one -- observed twice on
   the same idea: attempt 1 missed the closing question, attempt 2 added it and
   simultaneously grew FB hashtags and a 6th IG hashtag. The retry prompt now
   says to return the SAME plan with only the listed problems corrected.
2. **One retry is not enough.** Each pass does fix what it was told about, so
   the ceiling is `MAX_ATTEMPTS`, not one -- with surgical retries the drift
   that made extra attempts pointless is gone.

Parsing the model's JSON is delegated to `lib.crew.structured_output`. The
local `_strip_code_fence`/`_iter_balanced_json_object_candidates` copies were
kept independent on purpose; in practice they diverged into three tiers of
robustness, leaving this stage without the lenient repair `writer` had. See
`docs/adr/0007-one-crew-output-parser.md`.
"""

from __future__ import annotations

from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.rules import find_caption_violations
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

# Total kickoffs allowed per idea: the first draft plus two corrections. Each
# correction is one DeepSeek call (~7s), so the worst case stays well inside
# the orchestrator's per-idea budget.
MAX_ATTEMPTS = 3


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Delegates to the one crew output parser -- see `lib.crew.structured_output`.

    Kept as a thin wrapper so this stage's log lines keep its own logger name.
    """
    return parse_structured_output(raw, model, event=event, log=logger)


class KickoffError(Exception):
    """The Crew call itself failed -- network, auth, LiteLLM, quota.

    Distinct from "the model answered, but the answer was unusable". Retrying
    the former just multiplies the wait against a failure that will not fix
    itself (a revoked API key retried three times is three 401s and triple the
    time-to-diagnose); retrying the latter is the whole point, since malformed
    or schema-short output is non-deterministic drift.
    """


def _kickoff_and_parse(agent: Agent, task: Task) -> SocialPostPlan | None:
    """`None` when the model answered unusably (retryable by the caller).

    Raises `KickoffError` when the call never produced an answer at all.
    """
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_socialpost_kickoff_failed", error=str(exc))
        raise KickoffError(str(exc)) from exc

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, SocialPostPlan, event="crew_socialpost")


def _correction_prompt(plan: SocialPostPlan, violations: list[str]) -> str:
    """Feedback for one retry: hand back the rejected draft and ask for a
    minimal correction.

    Handing the draft back verbatim is the point. Without it the model writes
    a fresh plan from the original brief and re-breaks rules it had already
    satisfied; with it, the task is an edit rather than a rewrite.
    """
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "\n\n---\nYOUR PREVIOUS DRAFT WAS REJECTED. It was correct except for the "
        "problems listed below.\n\nPrevious draft:\n"
        f"{plan.model_dump_json(indent=2)}\n\n"
        f"Problems to fix:\n{bullets}\n\n"
        "Return the SAME plan with ONLY those problems corrected. Keep every other "
        "field and sentence exactly as it was -- do not rewrite the captions, do not "
        "change the hashtags unless a problem above is about hashtags, and do not "
        "introduce any new rule violation while fixing these."
    )


def execute_social_post_crew(
    agent: Agent, task: Task, *, target_keyword: str
) -> SocialPostPlan | None:
    """Run the real Social Post `Crew(...).kickoff()`, retrying with surgical
    feedback while the plan breaks hard caption rules. `None` if no attempt
    within `MAX_ATTEMPTS` produces a clean plan."""
    base_description = task.description
    plan: SocialPostPlan | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            plan = _kickoff_and_parse(agent, task)
        except KickoffError:
            # The call never landed. Already logged with its cause; retrying
            # would only multiply the wait against the same failure.
            return None
        if plan is None:
            # The model answered, but the answer was empty / not JSON / missing
            # required fields. That is drift, and drift is what retries are for
            # -- it used to abandon the idea on attempt 1 while a WORSE outcome
            # (captions breaking hard rules) got all MAX_ATTEMPTS below.
            # There is no draft to hand back, so the description is reset to the
            # original brief rather than carrying correction feedback.
            if attempt == MAX_ATTEMPTS:
                logger.warning("crew_socialpost_unparseable_exhausted", attempts=attempt)
                return None
            logger.warning("crew_socialpost_unparseable_retrying", attempt=attempt)
            task.description = base_description
            continue
        violations = find_caption_violations(plan, target_keyword=target_keyword)
        if not violations:
            return plan
        if attempt == MAX_ATTEMPTS:
            logger.warning(
                "crew_socialpost_rule_violations_exhausted",
                attempts=attempt,
                violations=violations,
            )
            return None
        logger.warning(
            "crew_socialpost_rule_violations_retrying",
            attempt=attempt,
            violations=violations,
        )
        # Rebuild from the ORIGINAL description each time: appending to an
        # already-appended prompt would stack contradictory correction blocks
        # from earlier attempts.
        task.description = base_description + _correction_prompt(plan, violations)

    return None
