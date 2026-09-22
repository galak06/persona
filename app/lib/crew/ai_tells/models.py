"""Typed result of one AI-tell scan.

Separate module (one model per file, this repo's convention -- see
`lib.crew.editor.models`) because `lib.crew.ai_tells.detect` is the logic
and `lib.crew.validate` is the consumer; neither should have to import the
other to name a finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AiTellFinding:
    """One detected tell, quoted verbatim so a human can find it in the source.

    `rule` is the machine-readable bucket (used for logging and for the
    per-rule thresholds in `lib.crew.ai_tells.detect`); `excerpt` is the
    offending text exactly as it appears, never a paraphrase -- same contract
    as `QualityVerdict.stray_artifacts`, and for the same reason: a finding
    a human cannot locate in the draft is a finding they cannot act on.
    """

    rule: str
    excerpt: str
    detail: str = ""

    def describe(self) -> str:
        """One-line, log/reason-safe rendering."""
        suffix = f" -- {self.detail}" if self.detail else ""
        return f"{self.rule}: {self.excerpt!r}{suffix}"


@dataclass(frozen=True)
class AiTellReport:
    """Everything one scan found, split by how the gate should treat it.

    `blocking` findings are unambiguous template/meta artifacts -- a single
    instance is a defect (a heading literally reading "FAQ", an opener from
    the banned-formula list). `advisory` findings are density signals over
    words that have legitimate uses ("crucial", "landscape"): any one of them
    is fine, a pile of them is the tell. Keeping them in separate buckets is
    what lets the gate fail closed on the first kind without stalling the
    whole no-human-in-the-loop pipeline on the second.
    """

    blocking: list[AiTellFinding] = field(default_factory=list)
    advisory: list[AiTellFinding] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True when nothing blocking was found."""
        return not self.blocking

    def reasons(self) -> list[str]:
        """Blocking findings as gate-reason strings (`ValidationResult.reasons`)."""
        return [f.describe() for f in self.blocking]
