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

Four recovery layers, tried in order, each strictly more invasive:

  1. Parse as-is.
  2. Repair illegal escapes (`repair_invalid_escapes`), then parse.
  3. Escape stray double quotes inside string values
     (`repair_stray_quotes`), then parse.
  4. Extract the last brace-balanced object embedded in surrounding prose
     (reasoning models narrate around their answer), with and without the
     escape repair.

Layer 3 answers a second live loss, the same shape as the first. A writer
run produced a complete 1,748-word post and lost all of it to prose that
quoted a word::

    ...most packaging treats "probiotic" like it's a magic word...
                             ^ ends the JSON string 17KB early

Unescaped `"` inside a value is not an illegal escape, so layer 2 cannot
see it. The failure is intermittent in the worst way: it depends purely on
whether the model happened to use quotation marks.

Every function here is pure and total: nothing raises, nothing does I/O.
Callers log which layer succeeded so a recovered result is distinguishable
from a clean one -- recovery should be visible, not silent.
"""

from __future__ import annotations

import json
import re
from typing import Final

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

# The complete set of characters that may legally follow a backslash inside a
# JSON string (RFC 8259 §7). Anything else makes the document unparseable.
_VALID_ESCAPE_CHARS: Final[frozenset[str]] = frozenset('"\\/bfnrtu')

# How a payload was recovered, for the caller's log. `None` means "parsed
# clean, no repair needed".
REPAIR_ESCAPES: Final[str] = "escape_repair"
REPAIR_STRAY_QUOTES: Final[str] = "stray_quote_repair"
REPAIR_EMBEDDED: Final[str] = "embedded_object"
REPAIR_EMBEDDED_ESCAPES: Final[str] = "embedded_object+escape_repair"

# Characters that may legally follow a string's closing quote, ignoring
# whitespace. If a `"` inside a string is followed by anything else, it did
# not close the string -- the model wrote a quotation mark in its prose.
_CLOSERS: Final[frozenset[str]] = frozenset(",:}]")


def strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- models add one
    even when explicitly told not to, often enough to be worth defending
    against rather than failing the whole run over it."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def repair_invalid_escapes(text: str) -> str:
    """Make every illegal `\\x` sequence legal by escaping the backslash.

    Scanned character-by-character rather than by regex so an already-valid
    `\\\\` is consumed as one unit and left alone -- a naive substitution
    would corrupt it into `\\\\\\`, turning a valid document into a broken
    one. A trailing lone backslash is escaped the same way.

    The backslash is PRESERVED as a literal rather than dropped: we cannot
    know what the model meant, and keeping the character is the smaller,
    reversible edit. `\\h2>` therefore survives as the text `\\h2>` -- a
    visible artifact in the output, which is the correct trade against
    discarding the entire document, and one the quality gate downstream can
    still judge.
    """
    out: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        following = text[i + 1] if i + 1 < length else ""
        if following in _VALID_ESCAPE_CHARS and following:
            out.append(char)
            out.append(following)
            i += 2
        else:
            out.append("\\\\")
            i += 1
    return "".join(out)


def repair_stray_quotes(text: str) -> str:
    """Escape `"` characters that appear *inside* a JSON string value.

    Scans with a tiny state machine. Inside a string, a `"` is treated as the
    real closing quote only when the next non-whitespace character is one of
    `_CLOSERS`; otherwise the model wrote a quotation mark in its prose and
    the quote is escaped so the string survives intact. Backslash escapes are
    consumed as a unit, so an already-correct `\\"` is left alone.

    HEURISTIC, and deliberately the second-to-last resort. It is wrong for
    prose that quotes a phrase immediately before a legal delimiter --
    `he said "hello", then left` reads as a closing quote and stays broken.
    That case simply falls through to the next layer and, failing that, to
    `None`; the repair is only ever *returned* when the result actually
    parses. The cost of a bad guess is therefore a parse failure the caller
    already had, while the benefit is a complete post that would otherwise
    be discarded -- which is why this runs at all rather than being ruled
    out for being imperfect.

    Quotation marks are PRESERVED as escaped quotes rather than dropped: the
    prose reads as the model wrote it (`"probiotic"` stays quoted), matching
    `repair_invalid_escapes`'s choice to keep characters over discarding them.
    """
    out: list[str] = []
    i = 0
    length = len(text)
    in_string = False
    while i < length:
        char = text[i]
        if not in_string:
            out.append(char)
            in_string = char == '"'
            i += 1
            continue
        if char == "\\" and i + 1 < length:
            out.append(text[i : i + 2])  # consume an escape pair whole
            i += 2
            continue
        if char == '"':
            probe = i + 1
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if probe >= length or text[probe] in _CLOSERS:
                out.append(char)
                in_string = False
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


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

    # Stray quotes, applied on top of the escape repair rather than the raw
    # text: a payload can carry both defects, and by here the escape-only
    # attempt has already failed, so there is nothing to lose by stacking.
    quoted = repair_stray_quotes(repaired)
    if quoted != repaired:
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
