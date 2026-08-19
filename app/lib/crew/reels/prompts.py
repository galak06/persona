"""Task-description ("prompt") builder for the Reel Beats agent.

Split out of `lib.crew.reels.agent` purely for file-size discipline, same
role `lib.crew.idea.prompts` plays for its agent.

Engagement structure is grounded against real short-form-video retention
data (not assumed): the first 1-3 seconds drive ~80% of completion variance,
so beat 1 must be a pattern-interrupt/curiosity-gap/bold-claim, not a slow
lead-in. Beat 5 must be one clear, singular CTA to the site, on-screen.

Each beat also carries an `image_prompt` -- concrete visual direction for
OpenArt's per-beat image generation (`lib.crew.reels.openart_client`). If
that fails for any reason, the WP post's own hero image is reused for all 5
beats instead and `image_prompt` goes unused; the overlay text (headline/
subcopy) is identical either way, so this one agent/prompt serves both
outcomes without knowing in advance which one will run.

Each beat may also name the reference-photo collection its generated image
should be grounded in (`reference_category`), chosen from the labels passed in
`reference_categories`. Brands with no such library pass nothing and get a
prompt byte-identical to the one that predates the field.
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.crew.reels.models import REEL_BEAT_COUNT
from lib.crew.reference_vocabulary import catch_all_clause

MAX_HEADLINE_LINE_CHARS = 14
MAX_SUBCOPY_CHARS = 32


def _reference_category_section(categories: Sequence[str]) -> str:
    """The `reference_category` instructions -- empty string when the brand has
    no reference-photo library, so the prompt stays byte-identical to what it
    was before this field existed."""
    if not categories:
        return ""
    listed = "\n".join(f"  - {label}" for label in categories)
    return f"""
## Reference-photo collection (`reference_category`)
Every beat's image is generated from a real photo of the brand's actual dog. The brand \
keeps several collections of those photos, one collection per kind of scene:
{listed}
For each beat, set `reference_category` to the ONE collection whose scenes best match \
that beat's own `image_prompt`, copied verbatim from the list above. The closer the \
collection matches the scene you described, the more the generated frame actually looks \
like this dog in that setting -- a "lying on the couch" reference dragged into a trail \
shot fights the prompt instead of grounding it. Those collections are the only ones that \
exist: there is no "none of the above", and a name that is not on that list leaves the \
beat with NO generated frame at all -- it falls back to a stock photo of some other dog. \
So when nothing is a clean match, still pick the CLOSEST one on the list; a near-miss \
reference is a real photo of this dog, which beats no reference every \
time.{catch_all_clause(categories)}
"""


def build_reels_task_description(
    *,
    title: str,
    body: str,
    brand_voice: str,
    reference_categories: Sequence[str] = (),
) -> str:
    """The full prompt handed to the fallback Reel Beats agent's `Task`.

    `body` is the WP post's HTML-stripped content, already truncated by the
    caller (`scripts/crewai_reels_pipeline.py`, ~3,000 chars) -- enough
    material for 5 real distinct points without sending the whole post.
    """
    return f"""You are writing a {REEL_BEAT_COUNT}-beat, on-screen Reel script from a \
published blog post for generation in [OpenArt](https://openart.ai/home). Each beat \
consists of on-screen text overlaid on a high-impact visual frame (vertical 9:16 \
aspect ratio). There is no voiceover, so the typography and the imagery carry the \
entire viewer experience.

## Brand voice (every beat and caption must fit this voice)
{brand_voice or "(no voice guide available)"}

## Source post
### Title
{title}

### Body (truncated)
{body}

## Structure -- exactly {REEL_BEAT_COUNT} beats, in this exact order
1. **Hook** -- a pattern-interrupt, curiosity gap, or bold claim. The first seconds \
decide whether anyone keeps watching; never a slow, generic lead-in.
2. **Point** -- one real, specific point from the post.
3. **Point** -- a second real, specific point from the post.
4. **Point** -- a third real, specific point from the post.
5. **CTA** -- one clear, singular call-to-action: invite a DM (e.g. "DM me for the \
article") -- never "link in bio" or any phrase implying a clickable link. Not vague, \
not multiple asks.

## Hard limits on overlay text (enforced, violations get you rejected and re-prompted)
- headline: each line at most {MAX_HEADLINE_LINE_CHARS} characters (rendered in a \
chunky stroked font -- longer gets clipped off the canvas)
- subcopy: at most {MAX_SUBCOPY_CHARS} characters total

## Each beat's `image_prompt` (Optimized for OpenArt)
Write a concrete, highly-detailed image generation prompt tailored for OpenArt, grounded \
in what that beat actually says -- never invent a visual claim the source post doesn't \
make. Every prompt must explicitly define:
- **Subject:** A clear, specific focus. Live-confirmed failure mode: this image is \
generated with a real reference photo of the brand's actual dog, but that reference \
only grounds the dog's appearance when the dog is the described subject -- a prompt \
describing an unrelated scene (a phone, a screen, an article, packaging) gets the dog \
right but freely invents everything else, including fake brand names and fake text. \
Never describe a phone screen, website, article preview, product label, or any surface \
with readable text/branding -- the model will hallucinate a plausible-looking but \
entirely fake brand name on it. Keep every subject physical and real: the dog, the \
food, the person's hands, the kitchen -- nothing with text for the model to invent.
- **Action/Environment:** Grounded setting matching the beat's text.
- **Cinematography & Style:** Vertical 9:16 aspect ratio, specific camera angle/lens \
(e.g., macro, close-up, dramatic POV), and lighting (e.g., golden hour, studio soft \
lighting, moody neon).
- **Standalone design:** Each beat's image stands completely alone as a compelling \
frame; do not reference continuity or "next shots."
{_reference_category_section(reference_categories)}
## Captions
Write an `ig_caption` and `fb_caption` -- both end with one clear CTA: invite the \
reader to DM for the article (e.g. "DM me and I'll send you the article" -- Reels \
captions can't carry a clickable link, and replies are handled manually). Never \
invent a claim the source post doesn't actually make.
"""
