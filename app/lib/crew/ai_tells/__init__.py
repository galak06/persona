"""Deterministic "does this read as machine-written" scan for drafted posts.

Public surface is `scan_body` plus the two result types; everything else
(pattern lists, rhythm statistics) is an implementation detail.
"""

from __future__ import annotations

from lib.crew.ai_tells.detect import scan_body
from lib.crew.ai_tells.models import AiTellFinding, AiTellReport

__all__ = ["AiTellFinding", "AiTellReport", "scan_body"]
