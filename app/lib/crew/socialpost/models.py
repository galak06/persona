"""Pydantic output contract for the social-post crew's writer agent.

One agent, one output shape (`SocialPostPlan`): two platform captions plus the
direction for the single hook image both platforms share.

**One image, two captions.** The image is generated once
(`lib.crew.wp_image.generate_wp_image` + `text_overlay`) and posted to both
platforms; the captions are not interchangeable, because the platforms differ
in the one way that matters here -- Facebook renders an inline URL, Instagram
cannot make a link clickable at all. Writing one caption and reusing it would
mean either a dead URL sitting in the IG caption or no CTA on the FB post.

`target_question` is carried as its own field rather than left implicit in
`fb_caption` so the answer-first rule is a testable property of the output
instead of a hope about the prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SocialPostPlan(BaseModel):
    """Social Post Writer's output -- both captions plus image direction."""

    target_question: str = Field(
        description="The single real search-intent question this post answers, phrased the "
        "way a reader would actually type it (e.g. 'Is bone broth safe for dogs?'). "
        "Both captions must answer it in their first sentence."
    )
    fb_caption: str = Field(
        description="Facebook Page post body, 150-200 words, no hashtags. Answers "
        "target_question in sentence one with the target keyword in it. Carries the "
        "post URL once, late in the body -- never in the opening line. Ends with a "
        "genuine question to the reader, plus a separate short follow CTA."
    )
    ig_caption: str = Field(
        description="Instagram feed caption. Answers target_question in sentence one "
        "with the target keyword in it. Contains NO URL -- Instagram links are not "
        "clickable -- and never says 'link in bio'. Ends with a genuine question plus "
        "a separate short follow CTA, then 3-5 relevant hashtags on their own line."
    )
    overlay_headline: str = Field(
        description="Short on-screen headline painted over the image. May contain \\n "
        "for a deliberate line break."
    )
    overlay_subcopy: str = Field(description="Short on-screen subcopy line under the headline.")
    image_brief: str = Field(
        description="Concrete visual description for an AI image generator: specific "
        "subject, setting, mood, composition -- grounded in what this post actually "
        "says. Never a surface carrying readable text or branding."
    )
    cta_ribbon_text: str = Field(
        description="Very short all-caps CTA for the ribbon across the bottom of the "
        "image, e.g. 'FULL GUIDE -> DOGFOODANDFUN.COM'."
    )
    image_alt_text: str = Field(
        description="Plain description of the finished image for accessibility. Indexed "
        "by search, so it should read naturally and include the topic."
    )
