"""Sentence- and paragraph-rhythm statistics for one post body.

The "homogenous rhythm" tell is the one thing in this scan that cannot be
matched as a string: it is a property of the DISTRIBUTION of sentence
lengths, not of any single sentence. Human prose alternates fragments with
long winding clauses; a next-token predictor settles into a uniform
medium-length cadence.

Kept separate from `lib.crew.ai_tells.detect` because it is pure measurement
-- it reports numbers and holds no opinion about which numbers are bad. The
thresholds (and therefore the arguing) live in `detect`.

Coefficient of variation (stdev / mean) is used rather than raw stdev so the
number is comparable across posts of different average sentence length: a
post averaging 12-word sentences and one averaging 20-word sentences are
equally varied at the same CV.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

# Split on sentence-final punctuation followed by whitespace. Deliberately
# naive: abbreviations ("Dr.", "vs.") will over-split a little, which biases
# the measured variance DOWNWARD (more short fragments) and therefore makes
# the homogeneity check more forgiving, never falsely accusing.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SHORT_SENTENCE_WORDS = 6
_LONG_SENTENCE_WORDS = 30


@dataclass(frozen=True)
class RhythmMetrics:
    """Measured cadence of one body of prose. All ratios are 0.0-1.0."""

    sentences: int
    mean_words: float
    variation: float
    short_ratio: float
    long_ratio: float
    paragraph_variation: float

    def as_dict(self) -> dict[str, float]:
        """Log-friendly flat mapping (`AiTellReport.metrics`)."""
        return {
            "sentences": float(self.sentences),
            "mean_words": round(self.mean_words, 2),
            "variation": round(self.variation, 3),
            "short_ratio": round(self.short_ratio, 3),
            "long_ratio": round(self.long_ratio, 3),
            "paragraph_variation": round(self.paragraph_variation, 3),
        }


def _variation(values: list[int]) -> float:
    """Coefficient of variation, or 0.0 when it is undefined (<2 samples or
    an all-zero mean) -- 0.0 reads as "maximally uniform", which is the
    conservative answer for a body too short to judge."""
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(values) / mean


def measure_rhythm(text: str, paragraphs: list[str]) -> RhythmMetrics:
    """Measure sentence and paragraph cadence of already-stripped plain text.

    `text` is the whole body as prose; `paragraphs` is the same content split
    into blocks (the caller owns HTML parsing -- this module never sees tags).
    """
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    lengths = [len(s.split()) for s in sentences]
    if not lengths:
        return RhythmMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    para_lengths = [len(p.split()) for p in paragraphs if p.strip()]
    return RhythmMetrics(
        sentences=len(lengths),
        mean_words=statistics.mean(lengths),
        variation=_variation(lengths),
        short_ratio=sum(1 for n in lengths if n <= _SHORT_SENTENCE_WORDS) / len(lengths),
        long_ratio=sum(1 for n in lengths if n >= _LONG_SENTENCE_WORDS) / len(lengths),
        paragraph_variation=_variation(para_lengths),
    )
