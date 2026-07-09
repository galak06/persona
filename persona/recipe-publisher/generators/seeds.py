"""Recipe seed library — the ground truth for recipes that actually work.

Seeds live in `seeds/seeds.json` and their ingredients + steps are frozen.
The LLM is only allowed to write voice fields (intro, verdict, FAQ, captions)
around a matched seed. This prevents the pipeline from inventing recipes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_SEEDS_PATH = Path(__file__).parent.parent / "seeds" / "seeds.json"


@dataclass
class RecipeSeed:
    id: str
    title: str
    topic_keywords: list[str]
    category: str
    prep_minutes: int
    cook_minutes: int
    yield_servings: str
    tags: list[str]
    ingredients: list[str]
    steps: list[str]
    dog_safety_notes: str
    storage: str
    portion_guide: dict[str, str]
    source_attribution: str


class NoSeedMatchError(LookupError):
    """Raised when a topic has no acceptable seed match — stops LLM invention."""


def load_seeds(path: Path | None = None) -> list[RecipeSeed]:
    path = path or _SEEDS_PATH
    raw = json.loads(path.read_text())
    return [
        RecipeSeed(
            id=s["id"],
            title=s["title"],
            topic_keywords=list(s["topic_keywords"]),
            category=s["category"],
            prep_minutes=int(s["prep_minutes"]),
            cook_minutes=int(s["cook_minutes"]),
            yield_servings=s["yield_servings"],
            tags=list(s["tags"]),
            ingredients=list(s["ingredients"]),
            steps=list(s["steps"]),
            dog_safety_notes=s["dog_safety_notes"],
            storage=s["storage"],
            portion_guide=dict(s["portion_guide"]),
            source_attribution=s["source_attribution"],
        )
        for s in raw["seeds"]
    ]


# Common words that don't carry recipe signal. "dog" appears in every dog-food
# topic; "recipe" / "treat" / "homemade" are marketing boilerplate. Filtering
# these prevents spurious matches like "dog tacos" -> pb-banana-biscuits.
_STOPWORDS = frozenset(
    {
        "dog", "dogs", "recipe", "recipes", "treat", "treats", "food",
        "homemade", "easy", "simple", "quick", "best", "top", "favorite",
        "for", "the", "and", "with", "from", "make", "making",
    }
)


def _tokenize(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]+", text.lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def match_seed(
    topic: str,
    seeds: list[RecipeSeed] | None = None,
    *,
    min_score: float = 0.3,
) -> RecipeSeed | None:
    """Return the highest-scoring seed for `topic`, or None if nothing crosses the bar.

    Scoring only looks at the seed's curated `topic_keywords` — not the full title
    or category — so generic words in titles ("biscuits", "stew") don't create
    false positives. The topic must share at least one non-stopword with the
    seed's curated signal list AND cross `min_score` to match.
    """
    seeds = seeds or load_seeds()
    topic_tokens = _tokenize(topic)
    if not topic_tokens:
        return None

    best: tuple[float, RecipeSeed] | None = None
    for seed in seeds:
        kw_tokens = _tokenize(" ".join(seed.topic_keywords))
        overlap = len(topic_tokens & kw_tokens)
        if overlap == 0:
            continue  # need at least one real keyword hit
        score = overlap / max(len(topic_tokens), 1)
        if best is None or score > best[0]:
            best = (score, seed)

    if best is None or best[0] < min_score:
        return None
    return best[1]


def seed_to_body_sections(seed: RecipeSeed) -> dict[str, str]:
    """Pre-format the FROZEN sections of the post body from a seed.

    Returns markdown strings for the ingredient checklist, numbered instructions,
    portion guide, and storage. The LLM wraps these with its voice sections.
    """
    ingredients_md = "\n".join(f"- [ ] {line}" for line in seed.ingredients)
    steps_md = "\n".join(f"{i}. {step}" for i, step in enumerate(seed.steps, 1))
    portion_md = "\n".join(
        f"- **{size.capitalize()}:** {guide}"
        for size, guide in seed.portion_guide.items()
    )
    return {
        "ingredients": ingredients_md,
        "instructions": steps_md,
        "portion_guide": portion_md,
        "storage": seed.storage,
        "safety_note": seed.dog_safety_notes,
    }
