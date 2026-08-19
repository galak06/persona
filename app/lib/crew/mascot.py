"""Who -- or what -- the brand's mascot is, read from its own config.

This engine is multi-brand, so nothing here may assume a species. `config.json`
answers both halves of the question:

  * `site.mascot_name` -- what it is called ("Nalla", "Rusty", ""). Long-standing.
  * `site.mascot_kind` -- what KIND of thing it is ("dog", "cat", "cartoon fox",
    "delivery van", ""). Optional, free text, added because the image and vision
    prompts were hardcoding "dog" -- which quietly told every other brand that
    its mascot was one.

Both default to `""`, and every consumer has to stay correct with either or
both missing: a brand that names no mascot still gets asked "is the brand's own
mascot in this photo?", it just gets asked in general terms.

One reader rather than four: `scripts.reels_images`,
`scripts.crewai_content_pipeline`, `scripts.crewai_social_posts_pipeline` and
`lib.crew.reference_vision` each grew their own copy of this config read, and
they must agree -- the same generated post carries prompts built from all of
them. Tolerant by design: no config, an unreadable file, malformed JSON or a
non-object document all yield the empty mascot, because a cosmetic identity
field may never fail a pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lib.observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Mascot:
    """The brand's mascot as its config describes it. Both fields may be `""`."""

    name: str = ""
    kind: str = ""


#: The mascot of a brand that never configured one -- and of a brand whose
#: config could not be read. Callers cannot tell the two apart, deliberately.
NO_MASCOT = Mascot()


def read_mascot(brand_dir: Path) -> Mascot:
    """`site.mascot_name` / `site.mascot_kind` from the brand config.

    Never raises: any failure logs and returns `NO_MASCOT`.
    """
    try:
        config = json.loads((brand_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("brand_mascot_unreadable", brand_dir=str(brand_dir), error=str(exc))
        return NO_MASCOT
    site = config.get("site") if isinstance(config, dict) else None
    if not isinstance(site, dict):
        return NO_MASCOT
    return Mascot(
        name=str(site.get("mascot_name") or "").strip(),
        kind=str(site.get("mascot_kind") or "").strip(),
    )
