"""Who the brand IS, read from its own config -- its persona and its mascot.

This engine is multi-brand, so nothing here may assume a species, a gender, or
that either subject exists at all. `config.json` answers three questions:

  * `site.mascot_name` -- what the mascot is called ("Nalla", "Rusty", "").
  * `site.mascot_kind` -- what KIND of thing it is ("dog", "cat", "cartoon
    fox", "delivery van", ""). Optional, free text, added because the image and
    vision prompts were hardcoding "dog" -- which quietly told every other
    brand that its mascot was one.
  * `site.brand_persona` -- the PERSON behind the brand ("Nalla's Dad", ""),
    long-standing elsewhere in the engine (`lib.crew.context`,
    `lib.crew.writer.assemble`) and read here because that person appears in
    reference photos too. Without it a photo of them was treated as scenery,
    and the generated person drifted from post to post.

This module was `lib.crew.mascot` and its dataclass was `Mascot`; both were
renamed when the persona joined them, because "the brand's mascot" stopped
being an honest name for a record that also carries the brand's persona.

All three default to `""`, and every consumer has to stay correct with any of
them missing: a brand that names no mascot still gets asked "is the brand's own
mascot in this photo?", it just gets asked in general terms.

One reader rather than four: `scripts.reels_images`,
`scripts.crewai_content_pipeline`, `scripts.crewai_social_posts_pipeline` and
`lib.crew.reference_vision` each grew their own copy of this config read, and
they must agree -- the same generated post carries prompts built from all of
them. Tolerant by design: no config, an unreadable file, malformed JSON or a
non-object document all yield the empty identity, because a cosmetic identity
field may never fail a pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lib.observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BrandIdentity:
    """The brand's own subjects, as its config describes them.

    Every field may be `""`, and the three are independent: a brand may have a
    mascot and no persona, a persona and no mascot, both, or neither.
    """

    mascot_name: str = ""
    mascot_kind: str = ""
    persona_name: str = ""


#: The identity of a brand that configured none -- and of a brand whose config
#: could not be read. Callers cannot tell the two apart, deliberately.
NO_IDENTITY = BrandIdentity()


def read_brand_identity(brand_dir: Path) -> BrandIdentity:
    """`site.mascot_name` / `site.mascot_kind` / `site.brand_persona`.

    Never raises: any failure logs and returns `NO_IDENTITY`.
    """
    try:
        config = json.loads((brand_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("brand_identity_unreadable", brand_dir=str(brand_dir), error=str(exc))
        return NO_IDENTITY
    site = config.get("site") if isinstance(config, dict) else None
    if not isinstance(site, dict):
        return NO_IDENTITY
    return BrandIdentity(
        mascot_name=str(site.get("mascot_name") or "").strip(),
        mascot_kind=str(site.get("mascot_kind") or "").strip(),
        persona_name=str(site.get("brand_persona") or "").strip(),
    )
