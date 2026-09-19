"""The static spec text the writer prompt is assembled from.

Split out of `lib.crew.writer.prompts` for file-size discipline (that module
crossed 290 lines once the prose rules landed), on the same seam `prompts`
itself was split from `lib.crew.writer.context`: these are long formatted
constants, not logic. `prompts` builds; this module is what it builds FROM.

`_PROSE_RULES` and the heading ban in `_BLUEPRINT_SPEC` are the "ask" half of
the anti-AI-tell work -- `lib.crew.ai_tells` is the "enforce" half, and the
two are deliberately redundant. The prompt asks the model not to write a
heading called "FAQ"; the scanner rejects the draft if it did anyway. Keep
the banned lists here in sync with `lib.crew.ai_tells.vocabulary`.
"""

from __future__ import annotations

_BLUEPRINT_SPEC = """\
## Blueprint (every numbered item must appear, as WordPress-ready HTML)
Items 1-2 are fixed boilerplate and must be exact. Items 3-9 describe the JOB each \
section does; their headings, their lengths and the order of the closing blocks are \
yours to choose per post -- see "Headings: never name the slot" below.
1. Byline: "By {persona} / {today}"
2. Affiliate disclosure -- immediately after the byline. Use this EXACT sentence, verbatim: \
"{disclosure}"
3. Hook -- an 80-150 word opener matching the brief's mascot_angle, with the mascot in it.
   Do NOT open the post with a time-anchored retrospective anecdote. "Last spring/month/week/\
winter...", "Two weeks ago...", "A few months back...", "It started with...", "It was a \
normal Tuesday evening...", "I still remember the day...", "I'll be honest:..." and their \
variants are BANNED as the opening words. 16 of the 19 most recent live posts opened on one \
of those formulas, two of them ("I'll be honest:", posts 4156 and 4169) word-for-word \
identical -- it is the default reading of "a specific, relatable scenario", and on a listing \
page the cards read as one repeated template. Choose whichever of these fits THIS brief, and \
vary it from post to post:
   - the reader's own situation, present tense ("Your dog's breath clears a room...")
   - the number the post turns on ("Four weeks. 28 brushings. One unimpressed shepherd mix.")
   - the claim you are about to test, stated flat ("Dental chews are sold as a toothbrush \
replacement. They are not.")
   - the question the post actually answers ("Can changing food alone clean a dog's teeth?")
   - a scene in the present tense with no date attached ("Nalla clamps her jaw shut the second \
the toothbrush appears.")
4. Problem statement -- why this topic matters
5. Core content -- H2/H3 sections per the brief's outline, data-heavy (at least 3 concrete \
numbers/data points per major section), engineer-metaphor framing
6. Product comparison table -- ONLY if the catalog below has products genuinely relevant to \
this topic; a markdown-in-HTML <table> with name/price-range/key-spec/pros/cons/affiliate link \
columns, using [AFFILIATE:key] placeholders for links. If nothing in the catalog fits, skip \
this section and the "Our Pick" section entirely -- do not force irrelevant products in.
7. A reader-question section -- one <h3> per question, each followed by a real answer \
paragraph; also return these as faq_pairs in your structured output (used to generate \
schema.org FAQPage markup separately -- do not add the JSON-LD yourself). Answer between 3 \
and 7 questions, and pick that NUMBER from the topic: a narrow gear comparison genuinely has \
three open questions, a diet-transition post has seven. Do not default to five every time.
8. A further-reading section -- a bulleted list of internal links, using ONLY the brief's real \
internal_link_candidates (never invent a URL)
9. A closing recommendation with an [AFFILIATE:key] link, ONLY if step 6 produced a product \
section

## Headings: never name the slot
Sections 6-9 above describe what each block DOES; they are not headings. Writing the slot name \
as the heading is the single loudest "a template generated this" signal a reader gets, and \
these exact strings are BANNED as headings (alone or as a prefix before a colon):
"FAQ", "FAQs", "Frequently Asked Questions", "Related Reading", "Related Posts", "Our Pick", \
"Product Comparison Table", "Comparison Table", "Hook", "Problem", "Problem Statement", \
"Introduction", "Conclusion", "Summary", "Key Takeaways", "Final Thoughts", "Core Content".
Every heading must be about THIS post's subject and mean something to someone skimming the \
page: "What Owners Keep Asking Me About Bully Sticks" instead of "FAQ"; "What I Actually Feed \
Her Now" instead of "Our Pick"; "If You Only Change One Thing" instead of "Conclusion". A \
draft is rejected outright for any banned heading -- this is checked mechanically, not judged.

Vary the ORDER of the closing blocks too. The last three sections do not have to run \
questions -> links -> pick every time; ending on the recommendation, on the links, or on the \
questions are all fine, and different posts should end differently.

Target 2,500-3,500 words for body_html. Report the actual word_count of what you wrote -- do \
not just restate the target.
"""

_PROSE_RULES = """\
## Prose: do not write like a language model
A reader can tell. What gives it away is never one word -- it is statistical smoothness, so \
these are constraints on the TEXTURE of the whole post, and several are checked mechanically \
before it can be published.

**Vary the cadence.** Alternate real fragments, short declaratives and long winding sentences \
inside the same paragraph. A post whose sentences are all 15-20 words reads hypnotic and \
machine-even no matter how good the content is. At least one sentence in twenty should be six \
words or shorter. Vary paragraph length the same way -- a one-line paragraph is allowed and is \
often the strongest thing on the page.

**Do not use these transitions.** They are the stitching a predictor reaches for and a person \
does not: "Furthermore", "Moreover", "Additionally", "It is important to note", "It's worth \
noting", "In summary", "In conclusion", "To summarize", "Ultimately", "That said", "At the end \
of the day", "The bottom line is", "Here's the thing", "The truth is", "Needless to say", \
"First and foremost", "Last but not least", "Simply put", "Rest assured", "Navigating...". \
Connect ideas by making the next sentence follow from the last one, or just start the new \
thought cold.

**Do not use these words.** They appear in LLM prose at many times their rate in human \
writing: delve, tapestry, testament, beacon, crucial, pivotal, paramount, myriad, plethora, \
robust, seamless, holistic, landscape (figurative), realm, leverage (as a verb), underscore, \
elevate, unlock, game-changer, deep dive, treasure trove, cornerstone, multifaceted, \
intricate, meticulous, embark, foster, nuanced, comprehensive, invaluable, unwavering, \
ever-evolving, fast-paced.

**Avoid the antithesis reflex.** "It's not just X, it's Y", "more than just a...", "It's not \
about X, it's about Y" -- this construction is a signature rhythm. Once in a post is a \
stylistic choice; three times is a fingerprint.

**Break the symmetry.** Do not give every section the same shape. Sections should differ in \
length -- some three paragraphs, one a single paragraph, one carrying a table. Do not open \
every section with a topic sentence and close it with a summarizing one. Do not default to \
three bullets or three examples every time you list something; if there are four real ones, \
list four, and if there are two, list two.

**Leave the friction in.** Specific, unglamorous, checkable detail is what generated prose \
cannot fake: the actual brand on the shelf, the price you paid, the week it went wrong, the \
measurement that came back boring. Say what didn't work and what you still don't know. A \
reservation, an unresolved question, or a genuinely odd comparison is worth more here than \
another balanced summary -- do not smooth the post into a consensus that offends nobody and \
tells nobody anything.
"""

_HARD_RULES = """\
## Hard rules
- Never claim implied vet/nutritionist/doctor credentials, a disease cure/treatment, dosage/\
prescription advice, or an absolute health-efficacy claim ("guaranteed", "100% safe", "cures").
- Never state a specific fact about the brand's mascot (diet, products owned, a specific \
anecdote) that isn't grounded in the mascot facts below -- if a detail isn't listed there, \
keep mascot mentions general ("in our experience", "we've noticed").
- Never invent a [AFFILIATE:key] -- only use keys listed in the product catalog below.
- Never invent an internal link URL -- only use the brief's internal_link_candidates.
- Include the current year ({year}) in the title.
"""
