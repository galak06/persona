"""One-focus-category content strategy — the brand-declared constraint that
scopes idea generation to a single category at a time.

Engine-level mechanism only. The category NAME is never known here: it comes
from the brand's own registry row (`brands.focus_category`) and is rendered
into `<brand_dir>/config.json`'s `content_strategy` block at provisioning,
the same DB-is-authoritative path `headless`/`group_join_limit`/`keywords`
already follow. An unset/empty focus is the default and means "no focus" —
today's breadth behaviour, byte-for-byte unchanged.

Why a category rather than another keyword list: the brand's real WordPress
categories are already the taxonomy `lib.crew.categorizer` files finished
posts into. Reusing that taxonomy keeps ONE category vocabulary per brand,
instead of a config-only list that drifts from the live site within a month.

The strategy this encodes (depth over breadth, one category at a time) is a
per-brand choice, not an engine default. A brand with no traction yet may
legitimately want breadth, which is why `focus_category=""` stays the
shipped default and `depth_bias` only takes effect once a focus is set.
"""

from __future__ import annotations

from dataclasses import dataclass

# The prompt clause injected when a brand has NO focus set. Verbatim the
# instruction `lib.crew.idea.prompts` carried unconditionally before this
# module existed, so an unfocused brand's prompt is unchanged.
BREADTH_CLAUSE = (
    "Prefer subjects with no coverage yet over a stronger signal on a subject "
    "already covered. Breadth is the point: this brand's ideas skew heavily "
    "toward a few repeated themes, so an unexplored area at a moderate score "
    "beats a fifth take on a saturated one."
)


@dataclass(frozen=True)
class ContentStrategy:
    """A brand's content-strategy stance. Immutable; built from config/DB."""

    focus_category: str = ""
    depth_bias: bool = True
    review_at: str = ""

    @property
    def has_focus(self) -> bool:
        """True when this brand has declared a focus category.

        Everything focus-related is gated on this, so a brand that never sets
        one keeps the pre-focus behaviour rather than falling into an
        empty-string category that would match nothing and starve the queue.
        """
        return bool(self.focus_category.strip())


def normalize_category(name: str | None) -> str:
    """Comparison form for a category name: trimmed, casefolded, inner
    whitespace collapsed.

    WordPress category names reach us from three places that disagree on
    capitalisation and spacing (the live WP REST list, an operator typing into
    Brand Settings, and an LLM echoing one back), so equality has to be
    forgiving about those three things — and nothing else. It deliberately
    does NOT strip punctuation or singularise: "Dog Food" and "Dog Foods" are
    different categories on a real site, and silently merging them would put
    ideas in a category the brand never declared.
    """
    if not name:
        return ""
    return " ".join(str(name).split()).casefold()


def load_content_strategy(config: dict[str, object] | None) -> ContentStrategy:
    """Read the `content_strategy` block out of a raw brand config dict.

    Pure: the caller owns reading `config.json`. A missing block, a non-dict
    block, or a non-string category all degrade to the no-focus default
    rather than raising — a malformed strategy block must not be able to take
    a brand's whole idea pipeline down.
    """
    if not isinstance(config, dict):
        return ContentStrategy()
    block = config.get("content_strategy")
    if not isinstance(block, dict):
        return ContentStrategy()

    raw_category = block.get("focus_category")
    category = raw_category.strip() if isinstance(raw_category, str) else ""

    raw_review = block.get("review_at")
    review_at = raw_review.strip() if isinstance(raw_review, str) else ""

    depth = block.get("depth_bias")
    depth_bias = depth if isinstance(depth, bool) else True

    return ContentStrategy(focus_category=category, depth_bias=depth_bias, review_at=review_at)


def is_in_focus(category: str | None, strategy: ContentStrategy) -> bool:
    """Whether an idea in `category` is allowed under `strategy`.

    A brand with no focus accepts everything (today's behaviour). A brand WITH
    a focus rejects a blank category too: an idea that never got a category is
    not evidence that it belongs to the focused one, and letting it through
    would be the silent leak that makes the whole gate decorative.
    """
    if not strategy.has_focus:
        return True
    return normalize_category(category) == normalize_category(strategy.focus_category)


def focus_clause(strategy: ContentStrategy) -> str:
    """The prompt clause for the idea agent, chosen by the brand's stance.

    This is the one place the depth-vs-breadth instruction is decided. Before
    focus existed the breadth clause was hardcoded into the prompt, which made
    "go deeper on one category" unreachable no matter what any config said —
    a prompt sentence out-votes a config flag every time.
    """
    if not strategy.has_focus:
        return BREADTH_CLAUSE
    category = strategy.focus_category.strip()
    if not strategy.depth_bias:
        return (
            f'Every idea MUST belong to the "{category}" category — this brand is '
            f"focused there for now. Within that category, prefer subjects with no "
            f"coverage yet over ones already covered."
        )
    return (
        f'Every idea MUST belong to the "{category}" category — this brand is '
        f"focused there for now, and an idea outside it will be rejected before "
        f"it is stored. Depth is the point, not breadth: within that category, a "
        f"genuinely thorough treatment of a subject already touched on beats a "
        f"thin first pass at an adjacent one. A new angle still has to answer a "
        f"materially different question than what exists — going deeper is not "
        f"permission to restate a covered topic."
    )
