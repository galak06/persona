"""Lenient JSON parsing for LLM output that is *almost* valid.

Every crew stage in this package asks its model for raw JSON and parses
`task.output.raw` by hand (see `lib.crew.writer.agent`'s docstring for why
`Task(output_pydantic=...)` isn't used with DeepSeek). Models are imperfect
at that, and the cost of a strict parse is total: a finished, high-quality
result is thrown away over one stray character.

That is not hypothetical. A live writer run produced a complete 1,550-word
post -- correct structure, correct FAQ, three correctly-placed affiliate
links -- and lost all of it to::

    ...variables.</p>\\h2><h2>The Problem: ...
                    ^ `\\h` is not a legal JSON escape

`json.loads` refused the document, the writer stage returned `None`, and the
idea bounced back to `approved` as if nothing had been written. The model had
almost certainly meant `\\n<h2>` and dropped two characters.

Five recovery layers, tried in order, each strictly more invasive:

  1. Parse as-is.
  2. Repair illegal escapes (`repair_invalid_escapes`), then parse.
  3. Drop trailing commas (`strip_trailing_commas`), then parse.
  4. Escape stray double quotes inside string values
     (`repair_stray_quotes`), then parse.
  5. Extract the last brace-balanced object embedded in surrounding prose
     (reasoning models narrate around their answer), with and without the
     escape repair.

Layer 4 answers a second live loss, the same shape as the first. A writer
run produced a complete 1,748-word post and lost all of it to prose that
quoted a word::

    ...most packaging treats "probiotic" like it's a magic word...
                             ^ ends the JSON string 17KB early

Unescaped `"` inside a value is not an illegal escape, so layer 2 cannot
see it. The failure is intermittent in the worst way: it depends purely on
whether the model happened to use quotation marks.

Layer 3 answers a third, from the strategist rather than the writer::

    "notes": "Link to internal recipes and resources.",
    },
                                                     ^ comma before `}`

JSON forbids a trailing comma; JavaScript and Python both allow one, so
models emit them constantly. It runs BEFORE the quote repair despite being
listed later in that layer's history, because it is the safer of the two:
a comma directly before `}` or `]` outside a string is *unambiguously*
invalid, so removing it cannot change the meaning of a valid document.
The quote repair, by contrast, guesses (see its docstring).

This module owns the POLICY -- which repairs to try, in what order, and what
to call the one that won. The repairs themselves are pure `str -> str`
transforms in `lib.crew.json_repairs`, split out when the combined module
passed this repo's 300-line limit. All of them are re-exported here, so
`from lib.crew.json_recovery import repair_invalid_escapes` still works.

Every function here is pure and total: nothing raises, nothing does I/O.
Callers log which layer succeeded so a recovered result is distinguishable
from a clean one -- recovery should be visible, not silent.
"""

from __future__ import annotations

import json
import re
from typing import Final

from lib.crew.json_repairs import (
    repair_invalid_escapes,
    repair_stray_quotes,
    strip_trailing_commas,
)

__all__ = [
    "REPAIR_EMBEDDED",
    "REPAIR_EMBEDDED_ESCAPES",
    "REPAIR_ESCAPES",
    "REPAIR_STRAY_QUOTES",
    "REPAIR_TRAILING_COMMAS",
    "iter_balanced_json_object_candidates",
    "loads_lenient",
    "repair_invalid_escapes",
    "repair_stray_quotes",
    "strip_code_fence",
    "strip_trailing_commas",
]

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


# How a payload was recovered, for the caller's log. `None` means "parsed
# clean, no repair needed".
REPAIR_ESCAPES: Final[str] = "escape_repair"
REPAIR_TRAILING_COMMAS: Final[str] = "trailing_comma_repair"
REPAIR_STRAY_QUOTES: Final[str] = "stray_quote_repair"
REPAIR_EMBEDDED: Final[str] = "embedded_object"
REPAIR_EMBEDDED_ESCAPES: Final[str] = "embedded_object+escape_repair"



def strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- models add one
    even when explicitly told not to, often enough to be worth defending
    against rather than failing the whole run over it."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def iter_balanced_json_object_candidates(text: str) -> list[str]:
    """Every top-level, brace-balanced `{...}` substring, in order.

    Brace depth is tracked while skipping string contents, so a literal
    `{`/`}` inside a quoted value doesn't throw off the count. More robust
    than a first-`{`-to-last-`}` slice: a real DeepSeek response was observed
    to emit a complete object, then more chain-of-thought prose, then a
    second final object -- slicing across both captures neither.
    """
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for i, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None
    return candidates


def loads_lenient(text: str) -> tuple[object | None, str | None]:
    """`(payload, repair)` -- parse `text`, recovering from common LLM damage.

    `payload` is `None` when every layer failed. `repair` names the layer that
    worked (`None` when the text parsed clean), so callers can log a recovered
    parse instead of silently treating it as a normal one.
    """
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass

    repaired = repair_invalid_escapes(text)
    if repaired != text:
        try:
            return json.loads(repaired), REPAIR_ESCAPES
        except json.JSONDecodeError:
            pass

    # Each repair stacks on the previous one: a payload can carry several of
    # these defects at once, and by the time we reach each layer the cheaper
    # attempts have already failed, so there is nothing to lose by keeping
    # the earlier fix applied. Trailing commas before stray quotes -- the
    # comma rule is exact, the quote rule guesses.
    decommaed = strip_trailing_commas(repaired)
    if decommaed != repaired:
        try:
            return json.loads(decommaed), REPAIR_TRAILING_COMMAS
        except json.JSONDecodeError:
            pass

    quoted = repair_stray_quotes(decommaed)
    if quoted != decommaed:
        try:
            return json.loads(quoted), REPAIR_STRAY_QUOTES
        except json.JSONDecodeError:
            pass

    # Last candidate first: when a model emits a draft object, reconsiders in
    # prose, then emits a final one, the LAST is its actual answer.
    for candidate in reversed(iter_balanced_json_object_candidates(text)):
        try:
            return json.loads(candidate), REPAIR_EMBEDDED
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(repair_invalid_escapes(candidate)), REPAIR_EMBEDDED_ESCAPES
        except json.JSONDecodeError:
            continue

    return None, None
