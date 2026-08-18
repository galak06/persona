"""Pydantic output contracts for the reels crew's Beats agent.

One agent, one output shape (`ReelPlan`): a fixed 5-beat script, each beat
carrying both its on-screen overlay text AND an image-generation prompt.
Two source images then get produced for the same beats -- either 5 distinct
OpenArt-generated images (one per beat, `lib.crew.reels.openart_client`) or,
if that fails for any reason, the WP post's own hero image reused for all 5
-- both rendered through the identical overlay+slideshow pipeline
(`recipe-publisher/workers/worker_wp_ideas_reel.py`). See that module's
docstring for why a shared plan/two-image-sources split is simpler than two
separate agents.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

REEL_BEAT_COUNT = 5


class ReelBeat(BaseModel):
    """One frame of the slideshow: on-screen headline + subcopy (rendered via
    `recipe-publisher/generators/text_overlay.py::apply_overlay`) plus an
    image-generation prompt describing the visual this beat should show
    (used only when the OpenArt image path succeeds; ignored -- the WP
    hero image is reused instead -- when it doesn't)."""

    headline: str = Field(description="Short on-screen headline, ALL CAPS convention optional.")
    subcopy: str = Field(description="Short on-screen subcopy line.")
    image_prompt: str = Field(
        description="Concrete visual description for an AI image generator: specific "
        "subject, setting, mood, composition -- grounded in this beat's actual content, "
        "never vague mood words with nothing for the model to render."
    )
    reference_category: str = Field(
        default="",
        description="Which of the brand's reference-photo collections best matches this "
        "scene -- copy one value verbatim from the list supplied in the task, or leave empty.",
    )


class ReelPlan(BaseModel):
    """Reel Beats Agent's output -- fixed 5-beat hook/point/point/point/CTA
    arc plus both platform captions. `lib.crew.reels.execute` hard-validates
    `len(beats) == REEL_BEAT_COUNT`, mirroring
    `recipe-publisher/generators/carousel_drafter.py`'s own exact-count-validation
    precedent for its 6-slide output."""

    beats: list[ReelBeat] = Field(default_factory=list)
    ig_caption: str = Field(description="Instagram caption, ending in one clear CTA to the site.")
    fb_caption: str = Field(description="Facebook caption, ending in one clear CTA to the site.")
