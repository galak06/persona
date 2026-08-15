"""Buyer-intent query synthesis: a `ContentBrief` -> Amazon-style searches.

The first half of dynamic product discovery (`lib.crew.products.discovery`
is the second). One agent, one task, same `llm="deepseek/..."` wiring and
manual-JSON-parsing posture as `lib.crew.products.agent`'s selector.

**Why an LLM rather than a format string.** The obvious cheap implementation
-- glue the brief's `primary_keyword` to an intent suffix -- was built and
measured against the live API first, and it fails exactly where it has to
work. For the recall post that shipped with zero products:

    "dog food recall 2026 best for dogs site:amazon.com"  -> 0 ASINs
    "best dog food recall 2026 site:amazon.com"           -> 0 ASINs
    "dog food storage container site:amazon.com"          -> 4 ASINs
    "limited ingredient dog food site:amazon.com"         -> 4 ASINs

The first two are the post's own words rearranged; Google answers them with
news coverage, because that is what those words mean. The last two are what
a person who just read about a recall would go buy. Nothing mechanical gets
from the topic to the purchase -- that hop is a judgment about the reader,
which is what this agent is for.

Failure is never fatal: `None` (logged) leaves discovery with no queries,
and the pipeline falls back to the curated catalog exactly as before.
"""

from __future__ import annotations

import json
import re

from crewai import Agent, Task
from pydantic import ValidationError

from lib.crew.products.agent import _json_output_instructions
from lib.crew.products.models import ShoppingQueries
from lib.crew.writer.models import ContentBrief
from lib.observability import get_logger

logger = get_logger(__name__)

DEEPSEEK_MODEL = "deepseek/deepseek-chat"
MAX_QUERIES = 4

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def build_query_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """The shopping-query synthesist. No tools: it reasons from the brief."""
    return Agent(
        role="Affiliate shopping strategist",
        goal=(
            "Name the product categories a reader of a given article would realistically "
            "buy, so relevant Amazon listings can be found for that article."
        ),
        backstory=(
            "You have spent years turning editorial content into product recommendations "
            "that readers actually click. You know the difference between what an article "
            "is ABOUT and what its reader WANTS: someone reading about a food recall does "
            "not shop for 'recalls', they shop for airtight storage containers and "
            "limited-ingredient food. You never pad a list to look thorough -- when an "
            "article's reader would buy nothing, you say so."
        ),
        llm=model,
        verbose=False,
        allow_delegation=False,
    )


def build_query_task(agent: Agent, description: str) -> Task:
    return Task(
        description=description,
        agent=agent,
        expected_output="A JSON object matching the ShoppingQueries schema.",
    )


def build_query_task_description(*, brief: ContentBrief, max_queries: int = MAX_QUERIES) -> str:
    """The synthesist's prompt: one post plan -> buyer-intent searches."""
    sections = "\n".join(f"  - {s.heading}" for s in brief.outline[:8]) or "  (no outline)"
    secondary = ", ".join(brief.secondary_keywords[:6]) or "(none)"
    return f"""You are choosing what to search for on Amazon so that an article can
recommend genuinely useful products to its readers.

THE ARTICLE
  Title            : {brief.suggested_title}
  Primary keyword  : {brief.primary_keyword}
  Secondary        : {secondary}
  Sections:
{sections}

YOUR TASK
Write at most {max_queries} short Amazon product searches for things a reader of
THIS article would plausibly buy right after reading it.

RULES
  1. Search for PRODUCT CATEGORIES, not the article's subject. "dog food
     recall 2026" is a news topic and returns zero products; "dog food
     storage container" is something a person buys.
  2. 2-5 words each. No brand names, no "best", no year, no "site:" operator,
     no punctuation.
  3. Each query must name a DIFFERENT kind of product -- not three phrasings
     of one thing.
  4. Only include a query if a reader would genuinely want that product after
     reading this article. An empty list is the correct answer for a purely
     informational piece. Never invent a tenuous connection to fill the list.

{_json_output_instructions(ShoppingQueries)}"""


def _parse_queries(raw: str | None) -> ShoppingQueries | None:
    """Parse+validate the synthesist's raw output. `None` (logged) on any
    failure -- never raises, matching this package's "never crash the run"
    contract. An empty query list is a VALID answer, not a failure."""
    if not raw or not raw.strip():
        logger.warning("crew_products_queries_empty_output")
        return None
    match = _FENCE_RE.match(raw.strip())
    text = match.group(1) if match else raw.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("crew_products_queries_json_decode_failed", error=str(exc), raw_output=text)
        return None
    try:
        return ShoppingQueries.model_validate(payload)
    except ValidationError as exc:
        logger.warning("crew_products_queries_schema_validation_failed", error=str(exc))
        return None


def execute_query_crew(agent: Agent, task: Task) -> ShoppingQueries | None:
    """Run the real query-synthesis `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_products_queries_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_queries(raw)


def shopping_queries_for_brief(
    brief: ContentBrief,
    *,
    execute_fn: object | None = None,
    max_queries: int = MAX_QUERIES,
) -> list[str]:
    """Buyer-intent searches for one post. `[]` (logged) on any failure."""
    agent = build_query_agent()
    task = build_query_task(
        agent, build_query_task_description(brief=brief, max_queries=max_queries)
    )
    run = execute_fn if callable(execute_fn) else execute_query_crew
    result = run(agent, task)
    if result is None:
        return []
    cleaned = [q.strip() for q in result.queries if q and q.strip()][:max_queries]
    logger.info("crew_products_queries_built", title=brief.suggested_title, queries=cleaned)
    return cleaned
