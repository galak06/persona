"""Pydantic output contract for the social-post crew's writer agent.

One agent, one output shape (`SocialPostPlan`): two platform captions plus the
direction for the single hook image both platforms share.

**One image, two captions.** The image is generated once
(`lib.crew.wp_image.generate_wp_image` + `text_overlay`) and posted to both
platforms; the captions are not interchangeable -- different length budgets,
hashtag rules (none on FB, 3-5 on IG) and platform-specific prohibitions
("link in bio" phrasing is IG-only). Neither caption carries a URL: both
platforms reject/suppress page posts with caption links (live-confirmed on
this brand's accounts), so traffic runs through the comment-keyword CTA --
readers comment the keyword, the article link is delivered by DM, where links
ARE clickable and unpenalized. Fulfillment is the owner's existing manual
reply flow.

`target_question` and `comment_keyword` are carried as their own fields rather
than left implicit in the captions so the answer-first and comment-CTA rules
are testable properties of the output instead of hopes about the prompt.
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
    comment_keyword: str = Field(
        description="ONE topical word in ALL CAPS (e.g. 'BROTH') that readers comment "
        "to get the article DM'd to them. Must appear verbatim in both captions' "
        "closing comment-CTA. Replies are fulfilled manually -- never promise a bot "
        "or instant delivery."
    )
    fb_caption: str = Field(
        description="Facebook Page post body, 150-200 words, no hashtags, NO URL of any "
        "kind (Facebook rejects page posts with caption links). Sentence one answers "
        "target_question and reads as natural English clearly about the target keyword "
        "-- never the keyword phrase pasted in verbatim. May name the site in words "
        "once, late. Ends with a comment-keyword CTA ('Comment {comment_keyword} and "
        "I'll DM you the article') plus a separate short follow CTA."
    )
    ig_caption: str = Field(
        description="Instagram feed caption, NO URL of any kind, never 'link in bio'. "
        "Sentence one answers target_question in natural English clearly about the "
        "target keyword. Ends with the same comment-keyword CTA plus a separate short "
        "follow CTA, then 3-5 relevant hashtags on their own line."
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
    reference_category: str = Field(
        default="",
        description="Which of the brand's reference-photo collections best matches this "
        "scene -- copy one value verbatim from the list supplied in the task, or leave empty.",
    )
    cta_ribbon_text: str = Field(
        description="Very short all-caps CTA for the ribbon across the bottom of the "
        "image, e.g. 'FULL GUIDE -> DOGFOODANDFUN.COM'."
    )
    image_alt_text: str = Field(
        description="Plain description of the finished image for accessibility. Indexed "
        "by search, so it should read naturally and include the topic."
    )
