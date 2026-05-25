"""Hook-first caption validator — rejects captions starting with blocked phrases."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lib.errors.validation import ValidationFailedError  # type: ignore[import-not-found]  # noqa: E402

_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]")


def _first_sentence(caption: str) -> str:
    stripped = caption.lstrip()
    match = _SENTENCE_SPLIT_RE.search(stripped)
    end = match.start() if match else len(stripped)
    return stripped[:end].strip()


def validate_hook(caption: str, blocklist: list[str]) -> None:
    """Raise ValidationFailedError if caption's first sentence matches any blocklist regex.

    Args:
        caption: Full caption text.
        blocklist: Regex patterns (case-insensitive) — match against the first sentence.
    """
    if not caption or not blocklist:
        return
    first = _first_sentence(caption)
    if not first:
        return
    for pattern in blocklist:
        if re.compile(pattern, re.IGNORECASE).search(first):
            raise ValidationFailedError(
                f"Caption hook starts with blocked phrase: {first!r} matches {pattern!r}",
                violations=["hook_blocklist"],
            )
