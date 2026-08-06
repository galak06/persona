"""WordPress category resolution for CrewAI-drafted posts.

Split out of `lib.crew.draft` purely for file-size discipline -- this is the
"which real WP category (if any) does this post belong in" concern,
independent of "how to POST the draft and record the result".
"""

from __future__ import annotations

import html
from collections.abc import Callable

import httpx
from crewai import Agent, Task

from lib.crew.categorizer import (
    CategoryChoice,
    build_categorizer_agent,
    build_categorizer_task,
    build_categorizer_task_description,
    execute_categorizer_crew,
)
from lib.observability import get_logger

logger = get_logger(__name__)

CategorizeFn = Callable[[Agent, Task], CategoryChoice | None]


def resolve_category_id(
    client: httpx.Client,
    category_name: str,
    idea_id: str,
    *,
    title: str = "",
    categorize_fn: CategorizeFn = execute_categorizer_crew,
) -> int | None:
    """WP category lookup against the brand's real categories --
    `content_ideas.category` is free text from the scout/strategist (e.g.
    'Recipes', 'GPS/Gear', 'Nutrition'), not guaranteed to correspond to a
    real category name on this brand's site.

    Two passes:
    1. Exact, case-insensitive display-name match -- free, no LLM call,
       handles the common case ('Recipes' -> 'Recipes').
    2. If that misses AND a `title` is given: `lib.crew.categorizer`, a real
       DeepSeek call that reads the post's actual content and picks from the
       real category list (or none) -- catches genuine fits exact matching
       can't, e.g. 'Nutrition' -> 'Food & Diet' (shares zero literal words).
       The model's pick is validated against the real list before use --
       never trusted blindly, same posture as internal-link/affiliate-key
       validation elsewhere in this pipeline.

    Never raises, and never guesses wrong: an unmatched/unresolvable
    category just leaves the post uncategorized (safe, visible, fixable by
    hand) rather than forcing a bad fit. A lookup or categorizer failure is
    the same "no category" outcome as no match found.

    Requires a non-empty `category_name` to do anything at all (including
    the WP categories fetch) -- in real pipeline usage every scout-produced
    idea carries one, so this never actually limits real categorization; it
    just keeps a no-signal draft (`category_name=""`, e.g. `--idea-id`
    targeting a row with no category set) from paying for a categories
    fetch it has no chance of using.
    """
    if not category_name.strip():
        return None
    try:
        resp = client.get("/wp-json/wp/v2/categories", params={"per_page": 100})
        resp.raise_for_status()
        categories = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("crew_draft_category_lookup_failed", idea_id=idea_id, error=str(exc))
        return None

    id_by_name: dict[str, int] = {}
    real_names: list[str] = []
    for cat in categories:
        name = html.unescape(str(cat.get("name", ""))).strip()
        if name:
            real_names.append(name)
            id_by_name[name.casefold()] = int(cat["id"])

    exact = id_by_name.get(category_name.strip().casefold())
    if exact is not None:
        return exact

    if not title.strip() or not real_names:
        return None

    extra_context = (
        f"Original content-idea category label (may not exactly match a real "
        f"category -- use it as a hint, not a literal answer): {category_name.strip()}"
    )
    description = build_categorizer_task_description(
        title=title, extra_context=extra_context, real_category_names=real_names
    )
    agent = build_categorizer_agent()
    task = build_categorizer_task(agent, description)
    choice = categorize_fn(agent, task)
    if choice is None or not choice.category_name.strip():
        return None

    picked = id_by_name.get(choice.category_name.strip().casefold())
    if picked is None:
        logger.warning(
            "crew_draft_categorizer_invented_category", idea_id=idea_id, picked=choice.category_name
        )
        return None
    return picked
