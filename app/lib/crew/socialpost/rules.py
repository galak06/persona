"""Hard, checkable rules on a `SocialPostPlan`'s captions.

Kept out of `execute.py` so they can be tested directly against a fixture plan,
without a `Crew` or an LLM anywhere in the picture.

These are the constraints that are *structural* rather than stylistic -- the
ones where a violation makes the post wrong, not merely worse, and which a
language model reliably gets wrong often enough to be worth re-prompting over
(the same reasoning behind `lib.crew.reels.execute`'s beat-count retry). Tone,
voice and word choice are left to the prompt and the human review gate.

Returns a list of human-readable violation strings, which
`execute.execute_social_post_crew` feeds back verbatim into a single retry.
"""

from __future__ import annotations

import re

from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.prompts import IG_HASHTAG_MAX, IG_HASHTAG_MIN

_HASHTAG_RE = re.compile(r"#\w+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")
# Only real link syntax counts as a URL. Naming the site in words
# ("dogfoodandfun.com") is explicitly allowed in BOTH captions for recall --
# the platforms reject/suppress link syntax, not a domain spelled as words.
_LINK_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")

# Fraction of a keyword's meaningful words that must show up in the opening
# sentence. Deliberately NOT 1.0 and deliberately not a verbatim match: target
# keywords are SEO strings ("raw dog food dangers 2024"), and forcing them in
# whole produced openers like "What are the raw dog food dangers 2024?" --
# grammatically broken, and the first line is the one doing all the work.
# Two thirds keeps the post recognisably on-topic while leaving the writer
# room for real English.
KEYWORD_MIN_COVERAGE = 2 / 3

# Dropped before measuring coverage: they carry no topical signal, and a
# caption shouldn't be penalised for phrasing around them.
_KEYWORD_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "to",
        "in",
        "on",
        "with",
        "your",
        "you",
        "my",
        "is",
        "are",
        "be",
        "best",
        "how",
        "what",
        "why",
        "vs",
        "versus",
        "at",
        "by",
        "from",
    }
)


def first_sentence(text: str) -> str:
    """The caption's opening sentence -- the part that survives truncation."""
    stripped = text.strip()
    parts = _SENTENCE_END_RE.split(stripped, maxsplit=1)
    return parts[0].strip() if parts else stripped


def keyword_terms(target_keyword: str) -> list[str]:
    """The meaningful words of a target keyword, for coverage checking.

    Stopwords go, and so do bare years/numbers: "2024" in an SEO keyword is a
    freshness signal for search, not something a human says out loud in a
    caption's first line.
    """
    return [
        w
        for w in _WORD_RE.findall(target_keyword.lower())
        if w not in _KEYWORD_STOPWORDS and not w.isdigit() and len(w) > 2
    ]


def keyword_coverage(sentence: str, terms: list[str]) -> float:
    """Fraction of `terms` present in `sentence`, matched on a 4-char stem so
    singular/plural and simple inflections count ("danger" ~ "dangers")."""
    if not terms:
        return 1.0
    lowered = sentence.lower()
    hits = sum(1 for t in terms if t[: min(len(t), 4)] in lowered)
    return hits / len(terms)


def find_caption_violations(plan: SocialPostPlan, *, target_keyword: str) -> list[str]:
    """Every structural rule the plan breaks. Empty list means it's usable."""
    violations: list[str] = []
    keyword = target_keyword.strip().lower()

    fb = plan.fb_caption.strip()
    ig = plan.ig_caption.strip()

    if not fb:
        violations.append("fb_caption is empty.")
    if not ig:
        violations.append("ig_caption is empty.")
    if not fb or not ig:
        return violations

    # Answer-first: the opening sentence -- the part that survives truncation
    # and gets indexed -- has to be recognisably about the target keyword.
    # Coverage, not a verbatim paste: see KEYWORD_MIN_COVERAGE.
    terms = keyword_terms(keyword)
    if terms:
        for label, caption in (("fb_caption", fb), ("ig_caption", ig)):
            if keyword_coverage(first_sentence(caption), terms) < KEYWORD_MIN_COVERAGE:
                violations.append(
                    f"{label}'s FIRST sentence must be clearly about '{target_keyword}' -- "
                    f"work most of these words into it naturally: {', '.join(terms)}. "
                    "Write real English; do not paste the keyword phrase in verbatim."
                )

    # No link syntax anywhere -- FB and IG reject/suppress page posts whose
    # caption carries a URL (live-confirmed on this brand's accounts). The
    # site named in words is the only permitted pointer.
    if _LINK_RE.search(fb):
        violations.append(
            "fb_caption must contain no URL -- Facebook rejects page posts with "
            "caption links. Name the site in words instead."
        )
    if _LINK_RE.search(ig):
        violations.append(
            "ig_caption must contain no URL -- Instagram rejects caption links. "
            "Name the site in words instead."
        )

    if _HASHTAG_RE.search(fb):
        violations.append("fb_caption must contain no hashtags at all.")

    lowered_ig = ig.lower()
    for phrase in ("link in bio", "link in my bio", "check the bio", "bio link"):
        if phrase in lowered_ig:
            violations.append(
                f"ig_caption must never say '{phrase}' -- that phrasing suppresses reach."
            )
            break

    hashtags = _HASHTAG_RE.findall(ig)
    if not IG_HASHTAG_MIN <= len(hashtags) <= IG_HASHTAG_MAX:
        violations.append(
            f"ig_caption has {len(hashtags)} hashtags -- it needs between "
            f"{IG_HASHTAG_MIN} and {IG_HASHTAG_MAX}."
        )

    # The comment-keyword CTA is the traffic mechanism (links are banned
    # everywhere else) -- one ALL-CAPS word, present verbatim in both captions.
    ck = plan.comment_keyword.strip()
    if not ck or not ck.isupper() or not ck.isalpha() or " " in ck:
        violations.append("comment_keyword must be ONE topical word in ALL CAPS (e.g. 'BROTH').")
    else:
        if ck not in fb:
            violations.append(
                f"fb_caption must contain the comment-keyword CTA with '{ck}' verbatim."
            )
        if ck not in ig:
            violations.append(
                f"ig_caption must contain the comment-keyword CTA with '{ck}' verbatim."
            )

    # A closing question is a hard brand rule on every caption.
    if "?" not in fb:
        violations.append("fb_caption must end with a genuine question to the reader.")
    if "?" not in ig:
        violations.append("ig_caption must end with a genuine question to the reader.")

    return violations
