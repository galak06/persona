"""Which certification bodies a piece of spotlight copy actually CLAIMS.

Split out of ``rules.py`` (which has a 300-line ceiling) once the certification
half of rule 2 grew two things of its own: a matcher that knows a body by its
spelled-out name as well as its acronym, and a negation test strict enough to
be trusted on a post that publishes with no further human gate.

Why not reuse ``lib.medical_claims_validator._is_negated``, which ``rules.py``
called until 2026-09-18 and which ``lib.certification_claims`` still calls: it
is deliberately LOOSE. It suppresses a claim when any cue -- including the
dismissal words ``avoid``/``myth``/``debunk`` added on 2026-08-31 -- appears
anywhere in the previous SIX words, and its clause delimiters are only
``.!?;\\n``. That loosening is right for a 2,500-word article, where a false
positive throws the whole draft away and a human reads the result. It is wrong
here. Observed fail-opens on an UNVERIFIED product, every one of which shipped
a false certification claim to a live Facebook Page and Instagram account
because the crew's retry loop never saw a violation:

    "No more plaque, KONG Classic is VOHC accepted."   (cue 4 words back)
    "Debunking the myth: KONG Classic is VOHC accepted."
    overlay_subcopy = "No plaque, VOHC accepted"

So this module keeps the same SHAPE -- a cue in the clause before the token --
and tightens both halves: the cue must be ADJACENT (the two words immediately
before the body's name), and ``,``, ``:``, ``--`` and the dashes all end a
clause. The honest sentence the gate exists to permit still passes:
"these are not VOHC accepted".

The cost is stated plainly because it is real and it is the direction chosen on
purpose: a genuine negation phrased at arm's length -- "these are not on the
VOHC list" -- is now flagged, and the writer burns one crew retry rephrasing
it. A false certification claim is a false advertising claim; a rewritten
sentence is a retry. This gate fails CLOSED.

Pure: text in, acronyms out. No I/O, no LLM.
"""

from __future__ import annotations

import re
from typing import Final

from lib.certification_claims import CERTIFICATIONS
from lib.medical_claims_validator import _NEGATION_WORDS

#: The spelled-out name of each body. ``lib.certification_claims`` carries only
#: the acronyms, and matching only those let "Veterinary Oral Health Council
#: accepted" through untouched -- a claim a reader understands perfectly and a
#: regex did not. Defined here rather than there because the article gate reads
#: HTML where the expansion is usually a heading about what the seal means,
#: while a 40-word caption that spells it out is making the claim.
#: A body in ``CERTIFICATIONS`` with no entry here is still matched by acronym.
_EXPANDED_NAMES: Final[dict[str, str]] = {
    "VOHC": "Veterinary Oral Health Council",
    "AAFCO": "Association of American Feed Control Officials",
    "NASC": "National Animal Supplement Council",
}

#: Grammatical negations only. The dismissal cues (``avoid``, ``myth``,
#: ``beware``, ``debunk``) that ``medical_claims_validator`` folds into the same
#: tuple are deliberately NOT reused: "Debunking the myth: X is VOHC accepted"
#: is a certification claim, whatever the clause in front of it is doing.
_CERT_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _NEGATION_WORDS) + r")\b"
)

#: ``,``, ``:``, ``--`` and the en/em dashes are clause boundaries here and are
#: not in the medical gate. Every fail-open observed used one of them to put a
#: negation in a clause that had nothing to do with the claim.
_CLAUSE_DELIMITERS_RE: Final[re.Pattern[str]] = re.compile(r"--|[.!?;:,\n–—]")

#: Two, not six: a cue further back than "is not" / "are never" is a cue about
#: something else. See the module docstring for what this costs.
_NEGATION_WINDOW_WORDS: Final[int] = 2


def _normalise(text: str) -> str:
    """Lower-cased, whitespace collapsed -- so "AAFCO\\nAPPROVED" and a name
    broken across two overlay lines both look up the same key."""
    return " ".join(text.lower().split())


def _build_matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    """One pattern over every acronym and expanded name, plus the lookup that
    maps whatever matched back to the canonical acronym.

    Expansions are listed FIRST so the longer alternative wins, and their
    spaces become ``\\s+`` so a name wrapped across an overlay's two lines is
    still one match.
    """
    patterns: list[str] = []
    lookup: dict[str, str] = {}
    for body in CERTIFICATIONS:
        acronym = body.upper()
        expanded = _EXPANDED_NAMES.get(acronym)
        if expanded:
            patterns.append(r"\s+".join(re.escape(word) for word in expanded.split()))
            lookup[_normalise(expanded)] = acronym
        patterns.append(re.escape(body))
        lookup[_normalise(body)] = acronym
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE), lookup


_CERT_RE, _CERT_BY_TEXT = _build_matcher()


def is_negated(lowered_text: str, match_start: int) -> bool:
    """True when a negation cue sits in the two words immediately before the
    match, within the same clause.

    ``lowered_text`` is the whole field already lower-cased (the caller has it
    anyway) and ``match_start`` indexes into it. The typographic apostrophe is
    folded to the ASCII one first: a model writes "isn’t" as often as
    "isn't", and only one of them is in the cue list.
    """
    prefix = lowered_text[:match_start]
    clause_ends = [m.end() for m in _CLAUSE_DELIMITERS_RE.finditer(prefix)]
    clause = prefix[clause_ends[-1] if clause_ends else 0 :]
    words = clause.replace("’", "'").split()[-_NEGATION_WINDOW_WORDS:]
    return bool(_CERT_NEGATION_RE.search(" ".join(words)))


def claimed_certifications(text: str) -> list[str]:
    """Canonical acronyms ``text`` asserts, first appearance first, no repeats.

    A body named by its acronym ("VOHC") or by its full name ("Veterinary Oral
    Health Council") counts the same and is reported as the acronym, so a
    caller's message and its allow-list speak one vocabulary. Negated mentions
    are absent from the result: saying a product is NOT accepted is the safe
    claim this gate exists to keep sayable.
    """
    lowered = text.lower()
    claimed: list[str] = []
    for match in _CERT_RE.finditer(text):
        acronym = _CERT_BY_TEXT.get(_normalise(match.group(0))) or match.group(0).upper()
        if acronym in claimed or is_negated(lowered, match.start()):
            continue
        claimed.append(acronym)
    return claimed
