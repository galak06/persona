"""Unit tests for generators.text_normalize.unwrap_paragraphs. No network."""

from __future__ import annotations

from generators.text_normalize import unwrap_paragraphs


def test_hard_wrapped_prose_collapses_to_one_line() -> None:
    md = (
        "Nalla goes wild for these treats and so will\n"
        "your pup. They bake in under thirty minutes\n"
        "and freeze beautifully for later."
    )
    result = unwrap_paragraphs(md)
    assert "\n" not in result
    assert result == (
        "Nalla goes wild for these treats and so will your pup. "
        "They bake in under thirty minutes and freeze beautifully for later."
    )


def test_two_paragraphs_stay_separate() -> None:
    md = (
        "First paragraph line one\n"
        "first paragraph line two\n"
        "\n"
        "Second paragraph line one\n"
        "second paragraph line two"
    )
    result = unwrap_paragraphs(md)
    assert result == (
        "First paragraph line one first paragraph line two\n"
        "\n"
        "Second paragraph line one second paragraph line two"
    )
    # Exactly one blank line between the two paragraphs.
    assert result.count("\n\n") == 1


def test_bullet_list_and_heading_preserved_verbatim() -> None:
    md = (
        "## Ingredients\n"
        "\n"
        "- 2 lb beef liver\n"
        "- 1 cup oat flour\n"
        "- 1 egg"
    )
    result = unwrap_paragraphs(md)
    # No prose runs to join: every line is structure, so output is identical.
    assert result == md
    for line in ["## Ingredients", "- 2 lb beef liver", "- 1 cup oat flour", "- 1 egg"]:
        assert line in result.split("\n")


def test_fenced_code_block_preserved_verbatim() -> None:
    md = (
        "Intro prose line one\n"
        "intro prose line two\n"
        "\n"
        "```\n"
        "def foo():\n"
        "    return 1\n"
        "\n"
        "    # blank line above stays\n"
        "```\n"
    )
    result = unwrap_paragraphs(md)
    lines = result.split("\n")
    # Prose ahead of the fence is collapsed.
    assert "Intro prose line one intro prose line two" in lines
    # Code block interior is verbatim, including the internal blank line.
    fence_open = lines.index("```")
    assert lines[fence_open + 1] == "def foo():"
    assert lines[fence_open + 2] == "    return 1"
    assert lines[fence_open + 3] == ""
    assert lines[fence_open + 4] == "    # blank line above stays"
    assert lines[fence_open + 5] == "```"


def test_end_to_end_intro_collapses_list_untouched() -> None:
    md = (
        "Nalla's tail starts going the moment these hit\n"
        "the oven. Three pantry ingredients, one bowl,\n"
        "and a freezer-friendly batch that lasts weeks.\n"
        "\n"
        "## Ingredients\n"
        "\n"
        "- [ ] 2 lb beef liver\n"
        "- [ ] 1 cup oat flour\n"
        "- [ ] 1 egg\n"
    )
    result = unwrap_paragraphs(md)
    lines = result.split("\n")

    # Intro is now a single line, no intra-paragraph breaks.
    assert lines[0] == (
        "Nalla's tail starts going the moment these hit the oven. "
        "Three pantry ingredients, one bowl, and a freezer-friendly "
        "batch that lasts weeks."
    )
    # List lines untouched, each still on its own line.
    assert "- [ ] 2 lb beef liver" in lines
    assert "- [ ] 1 cup oat flour" in lines
    assert "- [ ] 1 egg" in lines
    assert "## Ingredients" in lines
