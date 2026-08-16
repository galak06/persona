"""Text-level repairs for *almost*-valid JSON emitted by an LLM.

Three pure `str -> str` transforms, each targeting one defect class that has
been observed to destroy a finished piece of work in production. None of them
parse: they hand a repaired string back to `lib.crew.json_recovery`, which
owns the try-in-order-and-parse policy and the labels for which layer won.

Split out of `json_recovery` because these are two different jobs -- *how to
repair damaged text* versus *which repairs to try and in what order* -- and
the combined module had grown past this repo's 300-line limit. Every name
here is re-exported from `json_recovery`, so existing imports keep working.

Each repair PRESERVES characters rather than deleting them: an unknown
backslash survives as a literal, a stray quote survives escaped. We cannot
know what the model meant, so the smaller reversible edit is correct, and a
visible artifact is a better outcome than a discarded document -- the quality
gate downstream can still judge it.
"""

from __future__ import annotations

from typing import Final

# The complete set of characters that may legally follow a backslash inside a
# JSON string (RFC 8259 §7). Anything else makes the document unparseable.
_VALID_ESCAPE_CHARS: Final[frozenset[str]] = frozenset('"\\/bfnrtu')

# Characters that may legally follow a string's closing quote, ignoring
# whitespace. If a `"` inside a string is followed by anything else, it did
# not close the string -- the model wrote a quotation mark in its prose.
_CLOSERS: Final[frozenset[str]] = frozenset(",:}]")


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


def strip_trailing_commas(text: str) -> str:
    """Drop commas that sit directly before a closing `}` or `]`.

    Unlike `repair_stray_quotes` this is not a guess: RFC 8259 has no
    trailing-comma production, so such a comma can only be model error.
    Removing it from an ALREADY-VALID document is a no-op, because a valid
    document cannot contain one -- which is why this layer runs first of
    the two.

    String-aware, so a comma inside a value (`"a, b]"`) is untouched, and
    escape pairs are consumed whole so a `\\"` cannot fool the scanner into
    thinking the string ended.
    """
    out: list[str] = []
    i = 0
    length = len(text)
    in_string = False
    while i < length:
        char = text[i]
        if in_string:
            if char == "\\" and i + 1 < length:
                out.append(text[i : i + 2])
                i += 2
                continue
            if char == '"':
                in_string = False
            out.append(char)
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == ",":
            probe = i + 1
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if probe < length and text[probe] in "}]":
                i += 1  # drop it, keeping the whitespace that follows
                continue
        out.append(char)
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
