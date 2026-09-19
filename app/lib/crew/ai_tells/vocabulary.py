"""The pattern data one AI-tell scan matches against -- no logic, just lists.

Split from `lib.crew.ai_tells.detect` so the lists can be reviewed, tuned and
tested as data (they will drift as model house-style drifts) without touching
the scanning code, mirroring how `lib.crew.reference_vocabulary` separates
label data from the clauses that use it.

Every entry here was chosen against real published dogfoodandfun posts rather
than from a generic "AI words" listicle: `_TEMPLATE_HEADINGS` is literally
what posts 4704/4728/4741 shipped, and the opener formulas are the ones the
writer prompt already had to ban by hand after 16 of 19 posts used them.
"""

from __future__ import annotations

# Headings that name the BLUEPRINT SLOT instead of this post's actual content.
# Matched as a prefix on the heading text, because the live offenders were
# "FAQ: Batch Cooking Dog Food, Answered" and "Our Pick: The Bottom Line" --
# a decorated template label is still a template label.
#
# These blocks are NOT being removed (they carry the FAQPage schema, the
# internal links and the affiliate link). The point is that their HEADINGS
# must be written for this post; the schema is built from the structured
# `faq_pairs` field in `lib.crew.writer.assemble`, never from the heading
# text, so renaming them costs nothing.
TEMPLATE_HEADINGS: tuple[str, ...] = (
    "faq",
    "faqs",
    "frequently asked questions",
    "related reading",
    "related posts",
    "related articles",
    "our pick",
    "product comparison",
    "comparison table",
    "hook",
    "problem",
    "problem statement",
    "introduction",
    "conclusion",
    "key takeaways",
    "final thoughts",
    "summary",
    "core content",
)

# Opening formulas that read as "generated first sentence". A post may not
# START on any of these. Kept in sync with the writer prompt's own banned list
# (`lib.crew.writer.prompts._BLUEPRINT_SPEC`) -- the prompt asks, this enforces.
BANNED_OPENERS: tuple[str, ...] = (
    "last spring",
    "last summer",
    "last fall",
    "last autumn",
    "last winter",
    "last month",
    "last week",
    "last year",
    "a few months back",
    "a few months ago",
    "a few weeks ago",
    "two weeks ago",
    "three weeks ago",
    "a couple of weeks ago",
    "it started with",
    "it all started",
    "it was a normal",
    "it was just another",
    "i still remember",
    "i'll be honest",
    "let me be honest",
    "let's be honest",
    "picture this",
    "imagine this",
    "we've all been there",
    "if you're like most",
    "in today's world",
    "in the world of",
    "when it comes to",
    "as a dog owner",
)

# Meta-commentary about the act of writing. Distinct from the editor agent's
# `stray_artifacts` check (which is an LLM judgment) -- these are the literal
# strings, caught deterministically so the gate does not depend on a second
# model noticing.
META_COMMENTARY: tuple[str, ...] = (
    "as an ai",
    "as a language model",
    "let me clarify",
    "to clarify further",
    "let me revise",
    "in this article, i will",
    "in this post, i will",
    "this article will explore",
    "this post will explore",
    "we will delve into",
    "let's dive in",
    "let's dive into",
    "by the end of this article",
    "in the sections below",
    "as mentioned above",
    "as i mentioned earlier",
    "as we discussed",
)

# The "transition trap": connective tissue a person rarely reaches for but a
# next-token predictor reliably does. Advisory (rate-based) -- one "that said"
# is prose, six is a machine stitching paragraphs together.
TRANSITION_TRAPS: tuple[str, ...] = (
    "furthermore",
    "moreover",
    "additionally",
    "in addition to this",
    "it is important to note",
    "it's important to note",
    "it is worth noting",
    "it's worth noting",
    "it is worth mentioning",
    "in summary",
    "in conclusion",
    "to summarize",
    "ultimately",
    "that said",
    "with that said",
    "at the end of the day",
    "the bottom line is",
    "here's the thing",
    "the truth is",
    "needless to say",
    "first and foremost",
    "last but not least",
    "on the other hand",
    "in other words",
    "simply put",
    "put simply",
    "rest assured",
    "navigating",
)

# Lexical clichés: words LLMs emit far above human base rate in this genre.
# Advisory for the same reason -- "crucial" is a real word, a post with nine
# of them plus "robust" and "landscape" is not a person's vocabulary.
LEXICAL_CLICHES: tuple[str, ...] = (
    "delve",
    "delving",
    "tapestry",
    "testament",
    "beacon",
    "crucial",
    "pivotal",
    "vital",
    "essential",
    "paramount",
    "myriad",
    "plethora",
    "robust",
    "seamless",
    "seamlessly",
    "holistic",
    "landscape",
    "realm",
    "leverage",
    "underscore",
    "underscores",
    "elevate",
    "unlock",
    "game-changer",
    "game changer",
    "deep dive",
    "treasure trove",
    "cornerstone",
    "multifaceted",
    "intricate",
    "meticulous",
    "meticulously",
    "embark",
    "foster",
    "nuanced",
    "comprehensive",
    "invaluable",
    "unwavering",
    "ever-evolving",
    "fast-paced",
)

# The antithesis construction ("not just X, but Y") -- a signature LLM rhythm.
# Regex rather than a literal because the payload between the halves varies.
ANTITHESIS_PATTERNS: tuple[str, ...] = (
    r"\bnot just\b[^.?!]{1,60}?\bbut\b",
    r"\bisn't just\b[^.?!]{1,60}?\bit's\b",
    r"\bis not just\b[^.?!]{1,60}?\bit is\b",
    r"\bmore than just\b",
    r"\bit's not about\b[^.?!]{1,60}?\bit's about\b",
)
