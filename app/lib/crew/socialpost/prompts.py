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
- **`reference_category` only when the brand has a reference-photo library.** The
  hook image is generated from the brand's own real photos, so naming the collection
  whose scenes match the brief is what makes that reference ground the scene instead
  of fighting it. Nothing here may assume the brand HAS a dog -- or a mascot at all
  (`lib.crew.brand_identity`); the mascot is anchored in code, on a `shows_mascot`
  photo (`lib.crew.reference_mascot`), precisely so this prompt does not have to ask
  for one. A brand with no library passes nothing and gets a prompt byte-identical to
  the one that predates the field.
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.crew.reference_vocabulary import catch_all_clause

MAX_HEADLINE_LINE_CHARS = 18
MAX_SUBCOPY_CHARS = 40
MIN_FB_CAPTION_WORDS = 150
MAX_FB_CAPTION_WORDS = 200
IG_HASHTAG_MIN = 3
IG_HASHTAG_MAX = 5


def _reference_category_section(categories: Sequence[str]) -> str:
    """The `reference_category` instructions -- empty string when the brand has
    no reference-photo library, so the prompt stays byte-identical to what it
    was before this field existed."""
    if not categories:
        return ""
    listed = "\n".join(f"  - {label}" for label in categories)
    return f"""
## Reference-photo collection (`reference_category`)
That image is generated from the brand's own real photos. The brand keeps several \
collections of them, one collection per kind of scene:
{listed}
Set `reference_category` to the ONE collection whose scenes best match the \
`image_brief` you just wrote, copied verbatim from the list above. That collection \
supplies the SETTING, so the closer it matches the scene, the more the finished image \
looks like this brand's own place -- a couch reference dragged into a kitchen scene \
fights the brief instead of grounding it. (The brand's mascot is anchored separately, \
on a photo that actually shows it, so you never have to pick a collection just to get \
the mascot right.) Those collections are the only ones that exist: there is no "none of \
the above", and a name that is not on that list means no image gets generated at all -- \
the post falls back to a stock photo. So when nothing is a clean match, still pick the \
CLOSEST one on the list; a near-miss reference is still a real photo of this brand, \
which beats no reference every time.{catch_all_clause(categories)}
"""


def build_social_post_task_description(
    *,
    title: str,
    body: str,
    target_keyword: str,
    site_domain: str,
    brand_voice: str,
    reference_categories: Sequence[str] = (),
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
Then ANSWER IT in the first sentence of both captions. No throat-clearing, no \
"let's talk about", no teaser that withholds the answer. Both platforms cut the \
caption off after a couple of lines and both index what's there, so the first \
sentence is doing almost all the work.

That first sentence must also be clearly about "{keyword}" -- work most of those \
words into it NATURALLY, as real English. Do NOT paste the keyword phrase in \
verbatim: it is a search string, not a sentence, and jamming it in whole produces \
broken openers like "What are the raw dog food dangers 2024?". Rephrase around it \
("Raw dog food carries real contamination risks..."), drop any year, and write the \
line you'd actually say to another dog owner.

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
- The caption MUST contain a real question mark. Ask the reader something \
genuine -- either open the comment CTA with it ("Want the full guide?") or add \
a short question of your own ("What does your dog do?"). A caption with no "?" \
is rejected.

## Instagram caption (`ig_caption`)
- Shorter than the Facebook one.
- NEVER write "link in bio", "check the bio", or anything implying a clickable \
link -- that phrasing gets the post's reach suppressed.
- End with the comment-keyword CTA, then a SEPARATE short follow invitation, \
then {IG_HASHTAG_MIN}-{IG_HASHTAG_MAX} relevant hashtags on their own final line.
- This caption MUST contain a real question mark too -- same rule as Facebook. \
Do not skip it just because the caption is shorter.

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
{_reference_category_section(reference_categories)}"""
