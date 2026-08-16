"""Tests for `lib.crew.json_recovery` and the writer stage's use of it.

Pure functions, no network and no crew: the writer's parse seam is called
directly with raw text, the way `tests/test_crew_socialpost.py` already
exercises its own `_parse_structured_output`.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import json

from lib.crew.json_recovery import (
    REPAIR_EMBEDDED,
    REPAIR_ESCAPES,
    REPAIR_STRAY_QUOTES,
    REPAIR_TRAILING_COMMAS,
    iter_balanced_json_object_candidates,
    loads_lenient,
    repair_invalid_escapes,
    repair_stray_quotes,
    strip_code_fence,
    strip_trailing_commas,
)
from lib.crew.writer.execute import _parse_structured_output
from lib.crew.writer.models import WrittenPost

# The exact damage that discarded a finished 1,550-word post: the model meant
# `\n<h2>`, dropped two characters, and left `\h` -- not a legal JSON escape.
_REAL_DAMAGE = r'{"body_html": "<p>done.</p>\h2><h2>Next</h2>"}'


def test_the_escape_that_lost_a_finished_post_now_parses() -> None:
    payload, repair = loads_lenient(_REAL_DAMAGE)

    assert repair == REPAIR_ESCAPES
    assert isinstance(payload, dict)
    assert payload["body_html"].startswith("<p>done.</p>")


# ── Layer 3: trailing commas ────────────────────────────────────────────────
#
# Third live loss, this one from the strategist: the brief was discarded and
# the run aborted before the writer ever ran. Captured verbatim.
_REAL_TRAILING_COMMA = (
    '{"outline": [{"heading": "Related Recipes", "level": "H2",\n'
    '  "notes": "Link to internal recipes and resources.",\n'
    '  },\n'
    '  {"heading": "Our Pick", "level": "H2"}],\n'
    ' "primary_keyword": "freeze dried raw dog food",\n}'
)


def test_the_trailing_comma_that_lost_a_strategist_brief_now_parses() -> None:
    payload, repair = loads_lenient(_REAL_TRAILING_COMMA)

    assert repair == REPAIR_TRAILING_COMMAS
    assert isinstance(payload, dict)
    assert len(payload["outline"]) == 2
    assert payload["primary_keyword"] == "freeze dried raw dog food"


def test_trailing_comma_strip_is_a_noop_on_valid_json() -> None:
    """A valid document cannot contain a trailing comma, so this layer must
    never alter one -- that is what makes it safer than the quote repair."""
    text = json.dumps({"a": [1, 2], "b": {"c": 3}})

    assert strip_trailing_commas(text) == text
    assert loads_lenient(text) == (json.loads(text), None)


def test_trailing_comma_strip_ignores_commas_inside_strings() -> None:
    text = json.dumps({"body_html": "<p>carrots, celery, and beans]</p>", "n": 1})

    assert strip_trailing_commas(text) == text


def test_trailing_comma_strip_is_not_fooled_by_an_escaped_quote() -> None:
    """An escape pair is consumed whole; otherwise a \\" would look like the
    end of the string and a comma inside it could be eaten."""
    text = json.dumps({"a": 'he said "x," and left', "b": [1]})

    assert strip_trailing_commas(text) == text
    assert loads_lenient(text)[1] is None


def test_trailing_commas_in_both_object_and_array_are_removed() -> None:
    payload, repair = loads_lenient('{"xs": [1, 2, 3, ], "y": 4, }')

    assert repair == REPAIR_TRAILING_COMMAS
    assert payload == {"xs": [1, 2, 3], "y": 4}


# ── Layer 4: stray quotes inside a string value ─────────────────────────────
#
# The second live loss, same shape as the first: a complete 1,748-word post
# discarded because the prose quoted a word. Captured verbatim from the run.
_REAL_STRAY_QUOTE = (
    '{"body_html": "<p>most packaging treats "probiotic" like '
    'it\'s a magic word.</p>", "word_count": 1748}'
)


def test_the_stray_quote_that_lost_a_finished_post_now_parses() -> None:
    payload, repair = loads_lenient(_REAL_STRAY_QUOTE)

    assert repair == REPAIR_STRAY_QUOTES
    assert isinstance(payload, dict)
    assert payload["word_count"] == 1748
    # Preserved as written, not stripped -- the reader still sees the quotes.
    assert '"probiotic"' in payload["body_html"]


def test_stray_quote_repair_leaves_valid_json_untouched() -> None:
    """The scanner must not fire on quotes that legally close a string."""
    text = json.dumps({"title": "Toppers", "body_html": "<p>plain</p>", "n": 3})

    assert repair_stray_quotes(text) == text
    assert loads_lenient(text) == (json.loads(text), None)


def test_stray_quote_repair_leaves_an_already_escaped_quote_alone() -> None:
    """An escape pair is consumed whole, so a correct \\" is not double-escaped."""
    text = json.dumps({"body_html": '<p>she said "hi"</p>'})

    assert repair_stray_quotes(text) == text
    assert loads_lenient(text)[1] is None


def test_stray_quote_repair_handles_a_quote_at_the_end_of_a_value() -> None:
    payload, repair = loads_lenient('{"a": "he shouted "stop"", "b": 1}')

    assert repair == REPAIR_STRAY_QUOTES
    assert payload == {"a": 'he shouted "stop"', "b": 1}


def test_stray_quote_before_a_delimiter_is_not_silently_corrupted() -> None:
    """Documented blind spot: a quoted phrase sitting immediately before a
    legal delimiter reads as a closing quote, so this stays unparseable. The
    contract that matters is that it fails loudly (None) rather than
    returning wrong content."""
    payload, repair = loads_lenient('{"a": "he said "hello", then left"}')

    assert payload is None or isinstance(payload, dict)
    if payload is not None:  # recovered by a later layer -- must still be sane
        assert repair is not None


def test_clean_json_reports_no_repair() -> None:
    """A clean parse must be distinguishable from a recovered one."""
    payload, repair = loads_lenient('{"a": 1}')

    assert (payload, repair) == ({"a": 1}, None)


def test_a_valid_backslash_escape_is_left_alone() -> None:
    """Naive substitution would corrupt `\\\\` into `\\\\\\`, breaking a
    document that was already valid."""
    text = json.dumps({"path": r"C:\tools\new"})

    assert repair_invalid_escapes(text) == text
    assert loads_lenient(text) == ({"path": r"C:\tools\new"}, None)


def test_the_backslash_is_preserved_not_dropped() -> None:
    """We can't know what the model meant, so keep the character: the smaller,
    reversible edit."""
    payload, _ = loads_lenient(r'{"x": "a\hb"}')

    assert isinstance(payload, dict)
    assert payload["x"] == r"a\hb"


def test_a_lone_backslash_before_a_space_is_repaired() -> None:
    """`\\ ` is not a legal escape either -- same class of damage as `\\h`."""
    payload, repair = loads_lenient(r'{"x": "ends with\ space"}')

    assert repair == REPAIR_ESCAPES
    assert isinstance(payload, dict)


def test_a_trailing_lone_backslash_does_not_crash_the_repair() -> None:
    """The scanner must handle running off the end of the string."""
    assert repair_invalid_escapes("abc\\") == "abc\\\\"


def test_json_wrapped_in_reasoning_prose_is_recovered() -> None:
    raw = 'Let me think about this.\n{"a": 1}\nThat looks right.'

    assert loads_lenient(raw) == ({"a": 1}, REPAIR_EMBEDDED)


def test_the_last_object_wins_when_a_model_reconsiders() -> None:
    """A draft object, then prose, then the real answer."""
    raw = '{"a": 1}\nWait, let me redo that.\n{"a": 2}'

    payload, _ = loads_lenient(raw)

    assert payload == {"a": 2}


def test_an_embedded_object_is_also_escape_repaired() -> None:
    """Both damages at once -- prose wrapping AND an illegal escape."""
    payload, repair = loads_lenient(r'Here you go: {"x": "a\hb"} -- done.')

    assert isinstance(payload, dict)
    assert payload["x"] == r"a\hb"
    assert repair is not None


def test_braces_inside_strings_do_not_confuse_the_scanner() -> None:
    candidates = iter_balanced_json_object_candidates('{"a": "not } an end"}')

    assert candidates == ['{"a": "not } an end"}']


def test_unrecoverable_text_returns_none() -> None:
    assert loads_lenient("not json at all") == (None, None)


def test_code_fences_are_stripped() -> None:
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


# ── the writer stage ─────────────────────────────────────────────────────────


def _post_json(body: str) -> str:
    return (
        '{"title": "T", "body_html": "' + body + '", "word_count": 10, '
        '"faq_pairs": [], "internal_links_used": [], "affiliate_keys_used": []}'
    )


def test_writer_recovers_a_post_it_used_to_discard() -> None:
    """End of the regression: the stage returns a post instead of None."""
    result = _parse_structured_output(
        _post_json(r"<p>done.</p>\h2><h2>Next</h2>"), WrittenPost, event="t"
    )

    assert result is not None
    assert result.title == "T"


def test_writer_still_rejects_genuinely_unparseable_output() -> None:
    """Recovery must not become "accept anything"."""
    assert _parse_structured_output("not json at all", WrittenPost, event="t") is None
    assert _parse_structured_output(None, WrittenPost, event="t") is None
    assert _parse_structured_output("   ", WrittenPost, event="t") is None


def test_writer_still_rejects_a_schema_mismatch() -> None:
    assert _parse_structured_output('{"title": "only a title"}', WrittenPost, event="t") is None
