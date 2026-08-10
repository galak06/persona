"""Task-description ("prompt") builder for the Social Post Writer agent.

Split out of `lib.crew.socialpost.agent` for file-size discipline, same role
`lib.crew.reels.prompts` plays for its agent.

The copy rules here are not stylistic preferences -- each one is load-bearing
given this brand's constraints, so they're spelled out rather than left to the
model's instincts:

- **Answer-first.** Both platforms truncate captions after a couple of lines,
  and both index what's in them. Opening by answering one real search-intent
  question serves the truncation problem, keyword placement, and citation by
  answer engines at the same time.
- **NO URL in either caption.** Live-confirmed on this brand's accounts: FB and
  IG reject/suppress page posts whose caption carries a raw link, and the two
  standard workarounds are also unavailable here (no link in the first comment,
  no bio-link change). The drive-to-site mechanism is therefore the site name
  spelled in words ({site_domain}-style recall, which the platforms don't treat
  as a link) plus the CTA ribbon painted onto the image itself.
- **Never "link in bio".** Explicitly telling people to go to the bio link is a
  documented way to get a post's reach suppressed. `lib.crew.reels.prompts`
  already encodes the same rule for reels.
- **The comment-keyword CTA is the traffic mechanism.** With links banned from
  captions, comments and bios, the one channel where a link IS clickable and
  unpenalized is a DM. "Comment BROTH and I'll DM you the article" turns each
  interested reader into a public comment (an engagement signal that lifts the
  post's reach) plus a DM conversation carrying the real link. Fulfillment is
  the owner's existing manual reply flow -- the captions must never promise a
  bot or instant delivery.
- **Two CTAs with different jobs.** The comment-keyword ask drives traffic; the
  follow CTA drives followers. Merged into one ask, they cannibalise each other.
- **Hashtags: none on FB, 3-5 on IG.** Note this is deliberately fewer than the
  6-8 in `app/CLAUDE.md`, which predates keywords-in-captions displacing hashtags
  as Instagram's discovery mechanism.
"""

from __future__ import annotations

MAX_HEADLINE_LINE_CHARS = 18
MAX_SUBCOPY_CHARS = 40
MIN_FB_CAPTION_WORDS = 150
MAX_FB_CAPTION_WORDS = 200
IG_HASHTAG_MIN = 3
IG_HASHTAG_MAX = 5


def build_social_post_task_description(
    *,
    title: str,
    body: str,
    target_keyword: str,
    site_domain: str,
    brand_voice: str,
) -> str:
    """The full prompt handed to the Social Post Writer's `Task`.

    `body` is the WP post's HTML-stripped content, already truncated by the
    caller (`scripts/crewai_social_posts_pipeline.py`) -- enough material to
    write from without sending the whole post.
    """
    keyword = target_keyword.strip() or title
    return f"""You are writing ONE Facebook Page post and ONE Instagram feed post \
from a published blog post. Both are accompanied by the same single image, whose \
art direction you also write. There is no video and no carousel.

## Brand voice (both captions must fit this voice)
{brand_voice or "(no voice guide available)"}

## Source post
### Title
{title}

### Target keyword (must appear verbatim in the first sentence of BOTH captions)
{keyword}

### Body (truncated)
{body}

## The single most important rule: answer first
Pick the one real question a reader would actually type into a search box that \
this post answers -- put it in `target_question`, phrased the way they'd type it. \
Then ANSWER IT in the first sentence of both captions, with "{keyword}" in that \
sentence verbatim. No throat-clearing, no "let's talk about", no teaser that \
withholds the answer. Both platforms cut the caption off after a couple of lines \
and both index what's there, so the first sentence is doing almost all the work.

## Hard rule for BOTH captions: no URL of any kind
Neither caption may contain a URL, a "www." address, or anything shaped like a \
link -- both platforms reject or suppress page posts whose caption carries one. \
You MAY name the site in words ({site_domain}) once, late in the caption, as \
recall ("full guide on {site_domain}") -- spelled as words, never as a link, \
never as an instruction to click anything.

## The traffic CTA: comment keyword
Pick ONE topical word in ALL CAPS for `comment_keyword` (e.g. BROTH, GEAR, \
HEAT) -- short, memorable, drawn from this post's actual subject. Both captions \
end with a comment-keyword CTA using it verbatim, phrased naturally in the brand \
voice: "Want the full guide? Comment {{keyword}} and I'll DM it to you." The DM \
is where the article link actually gets delivered (replies are handled \
personally by the owner -- NEVER promise a bot, automation, or instant delivery).

## Facebook caption (`fb_caption`)
- {MIN_FB_CAPTION_WORDS}-{MAX_FB_CAPTION_WORDS} words.
- NO hashtags at all.
- May name the site ({site_domain}) in words once, late in the body, after \
you've already delivered real value. Never in the first sentence.
- End with the comment-keyword CTA, then a SEPARATE short line inviting them to \
follow the page. Two distinct asks -- do not merge them into one sentence.

## Instagram caption (`ig_caption`)
- Shorter than the Facebook one.
- NEVER write "link in bio", "check the bio", or anything implying a clickable \
link -- that phrasing gets the post's reach suppressed.
- End with the comment-keyword CTA, then a SEPARATE short follow invitation, \
then {IG_HASHTAG_MIN}-{IG_HASHTAG_MAX} relevant hashtags on their own final line.

## Both captions
- Mention the brand's dog by name, as an actual anecdote from the post -- not a \
name-drop.
- Never use sales language ("check out our", "click here", "buy now", "shop now").
- Never make an absolute health claim, and never invent a claim the source post \
doesn't actually make.
- Never open with a generic hook like "great tips!" or "let's dive in".

## The image
`image_brief` is the art direction for one generated image (1:1). Describe a \
concrete physical scene grounded in what the post actually says: the dog, the \
food, hands, the kitchen, the trail. Give subject, setting, lighting and camera \
angle.

CRITICAL: never describe a phone screen, website, article preview, product label, \
packaging, or any surface with readable text on it. The image model will \
hallucinate a plausible-looking but entirely fake brand name onto it.

`overlay_headline` and `overlay_subcopy` are painted onto that image, so they must \
be short and readable at a glance:
- headline: each line at most {MAX_HEADLINE_LINE_CHARS} characters (use \\n to \
break lines deliberately) -- it renders in a chunky stroked font and longer text \
gets clipped off the canvas
- subcopy: at most {MAX_SUBCOPY_CHARS} characters total

`cta_ribbon_text` is a very short all-caps line for a ribbon across the bottom of \
the image (e.g. "FULL GUIDE  ->  {site_domain.upper()}").

`image_alt_text` describes the finished image in plain language for screen readers. \
It is also indexed by search, so write it as a real sentence including the topic -- \
not a keyword list.
"""
