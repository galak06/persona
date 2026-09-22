"""The AI-tell scan: one assembled post -> an `AiTellReport`.

Why this exists alongside the `lib.crew.editor` quality agent: the editor is
an LLM asked whether prose sounds machine-written, and a model grading its
own genre grades generously -- it passed 12 consecutive posts that all ended
on the identical "Frequently Asked Questions -> Related Reading -> Our Pick"
skeleton. This module is deterministic, so the same draft always gets the
same verdict and a regression is a failing test rather than a coin flip.

Two severities, because the tells are not equally certain:
  * BLOCKING -- a heading that names the template slot, an opener from the
    banned-formula list, meta-commentary about writing the post. Each of
    these is a defect on its first occurrence, with no legitimate reading.
  * ADVISORY -- density of clichés, transition traps and antithesis
    constructions, plus rhythm uniformity. Any single "crucial" is fine;
    the tell is the rate. These only block once they cross a per-1000-word
    rate calibrated against the brand's real published posts.

Thresholds are module constants rather than parameters because there is no
human in this pipeline to tune them per run (`lib.crew.validate`'s docstring)
-- they are a property of the gate, and changing one should be a reviewed
code change with a test, not a call-site argument.
"""

from __future__ import annotations

import html as html_lib
import re

from lib.affiliate_resolver import _has_disclosure
from lib.crew.ai_tells.models import AiTellFinding, AiTellReport
from lib.crew.ai_tells.rhythm import RhythmMetrics, measure_rhythm
from lib.crew.ai_tells.vocabulary import (
    ANTITHESIS_PATTERNS,
    BANNED_OPENERS,
    LEXICAL_CLICHES,
    META_COMMENTARY,
    TEMPLATE_HEADINGS,
    TRANSITION_TRAPS,
)
from lib.crew.draft_body import split_body_and_jsonld

# Rates are "occurrences per 1000 words". Calibrated against the 12 most
# recent live dogfoodandfun posts (2026-09-07): their measured cliché rate
# ranged 0.4-2.6/1k and transitions 0.5-3.1/1k, so these ceilings reject the
# genuinely saturated draft while leaving normal prose alone.
MAX_CLICHE_RATE = 4.0
MAX_TRANSITION_RATE = 4.0
MAX_ANTITHESIS_RATE = 2.0

# Below this coefficient of variation the sentence lengths are effectively
# one length repeated. Live posts measure 0.55-0.70; 0.35 is well clear of
# the brand's real floor and only fires on genuinely flat cadence.
MIN_SENTENCE_VARIATION = 0.35
# A body with no fragments at all reads as machine-even regardless of CV.
MIN_SHORT_SENTENCE_RATIO = 0.05
# Under this word count the statistics are noise, so rhythm is not judged.
MIN_WORDS_FOR_RHYTHM = 400

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script.*?</script>", re.DOTALL | re.IGNORECASE)
_HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_BYLINE_PREFIXES = ("by ",)


def _plain(fragment: str) -> str:
    """HTML fragment -> collapsed plain text."""
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _headings(body: str) -> list[str]:
    return [_plain(m.group(2)) for m in _HEADING_RE.finditer(body)]


def _paragraphs(body: str) -> list[str]:
    return [text for m in _PARAGRAPH_RE.finditer(body) if (text := _plain(m.group(1)))]


def _first_prose_paragraph(paragraphs: list[str]) -> str:
    """The first paragraph that is actually the post's opener.

    Skips the byline and the affiliate disclosure, which the blueprint
    mandates as paragraphs 1 and 2 -- the same skip `derive_excerpt` makes,
    and for the same reason: without it every post's "opener" is "By Nalla's
    Dad" and the banned-formula check never sees the real first sentence.
    """
    for text in paragraphs:
        if text.lower().startswith(_BYLINE_PREFIXES) or _has_disclosure(text):
            continue
        return text
    return ""


def _rate(count: int, words: int) -> float:
    return (count * 1000.0 / words) if words else 0.0


def _scan_headings(headings: list[str]) -> list[AiTellFinding]:
    """Headings that name the blueprint slot rather than this post's content."""
    findings = []
    for heading in headings:
        normalized = re.sub(r"[^a-z0-9 ]+", " ", heading.lower()).strip()
        for label in TEMPLATE_HEADINGS:
            if normalized == label or normalized.startswith(f"{label} "):
                findings.append(
                    AiTellFinding(
                        rule="template_heading",
                        excerpt=heading,
                        detail=(
                            f"heading names the blueprint slot ({label!r}); "
                            "write a heading about this post's actual content"
                        ),
                    )
                )
                break
    return findings


def _scan_opener(opener: str) -> list[AiTellFinding]:
    """The post's true first sentence against the banned opener formulas."""
    lowered = opener.lower().lstrip("\"'“‘ ")
    for formula in BANNED_OPENERS:
        if lowered.startswith(formula):
            return [
                AiTellFinding(
                    rule="banned_opener",
                    excerpt=opener[:160],
                    detail=f"opens on the banned formula {formula!r}",
                )
            ]
    return []


def _scan_phrases(text_lower: str, phrases: tuple[str, ...], rule: str) -> list[AiTellFinding]:
    """One finding per distinct phrase present, carrying its own count."""
    findings = []
    for phrase in phrases:
        count = len(re.findall(rf"\b{re.escape(phrase)}\b", text_lower))
        if count:
            findings.append(AiTellFinding(rule=rule, excerpt=phrase, detail=f"used {count}x"))
    return findings


def _phrase_count(text_lower: str, phrases: tuple[str, ...]) -> int:
    return sum(len(re.findall(rf"\b{re.escape(phrase)}\b", text_lower)) for phrase in phrases)


def _antithesis_count(text_lower: str) -> int:
    return sum(len(re.findall(p, text_lower)) for p in ANTITHESIS_PATTERNS)


def _rhythm_findings(metrics: RhythmMetrics, words: int) -> list[AiTellFinding]:
    """Uniform-cadence findings, or none when the body is too short to judge."""
    if words < MIN_WORDS_FOR_RHYTHM:
        return []
    findings = []
    if metrics.variation < MIN_SENTENCE_VARIATION:
        findings.append(
            AiTellFinding(
                rule="uniform_sentence_rhythm",
                excerpt=f"sentence-length variation {metrics.variation:.2f}",
                detail=(
                    f"below {MIN_SENTENCE_VARIATION:.2f}; sentences average "
                    f"{metrics.mean_words:.0f} words with little deviation -- "
                    "break the cadence with fragments and long clauses"
                ),
            )
        )
    if metrics.short_ratio < MIN_SHORT_SENTENCE_RATIO:
        findings.append(
            AiTellFinding(
                rule="no_short_sentences",
                excerpt=f"short-sentence ratio {metrics.short_ratio:.2f}",
                detail=(
                    f"below {MIN_SHORT_SENTENCE_RATIO:.2f}; the post contains "
                    "almost no sentences of six words or fewer"
                ),
            )
        )
    return findings


def scan_body(body_html: str) -> AiTellReport:
    """Scan one assembled post body for AI-tell patterns.

    Accepts the full assembled HTML (trailing JSON-LD included -- it is split
    off and ignored, so schema text never counts toward a prose rate). Never
    raises: a scanner exception inside a gate whose job is failing closed
    would itself be a fail-open bug, so every step is regex-and-string only.
    """
    body, _ = split_body_and_jsonld(body_html)
    body = _SCRIPT_RE.sub(" ", body)
    paragraphs = _paragraphs(body)
    text = _plain(body)
    text_lower = text.lower()
    words = len(text.split())

    metrics = measure_rhythm(text, paragraphs)
    cliche_rate = _rate(_phrase_count(text_lower, LEXICAL_CLICHES), words)
    transition_rate = _rate(_phrase_count(text_lower, TRANSITION_TRAPS), words)
    antithesis_rate = _rate(_antithesis_count(text_lower), words)

    blocking: list[AiTellFinding] = []
    blocking.extend(_scan_headings(_headings(body)))
    blocking.extend(_scan_opener(_first_prose_paragraph(paragraphs)))
    blocking.extend(_scan_phrases(text_lower, META_COMMENTARY, "meta_commentary"))
    blocking.extend(_rhythm_findings(metrics, words))

    advisory = _scan_phrases(text_lower, LEXICAL_CLICHES, "lexical_cliche") + _scan_phrases(
        text_lower, TRANSITION_TRAPS, "transition_trap"
    )
    for rate, ceiling, rule in (
        (cliche_rate, MAX_CLICHE_RATE, "cliche_density"),
        (transition_rate, MAX_TRANSITION_RATE, "transition_density"),
        (antithesis_rate, MAX_ANTITHESIS_RATE, "antithesis_density"),
    ):
        if rate > ceiling:
            blocking.append(
                AiTellFinding(
                    rule=rule,
                    excerpt=f"{rate:.1f} per 1000 words",
                    detail=f"exceeds the {ceiling:.1f}/1000 ceiling",
                )
            )

    return AiTellReport(
        blocking=blocking,
        advisory=advisory,
        metrics={
            **metrics.as_dict(),
            "words": float(words),
            "cliche_rate": round(cliche_rate, 2),
            "transition_rate": round(transition_rate, 2),
            "antithesis_rate": round(antithesis_rate, 2),
        },
    )
