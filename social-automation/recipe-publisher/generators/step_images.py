"""Interleave selected images under recipe instruction steps in the WP body.

Each image is placed as an indented HTML ``<figure>`` block under every Nth
numbered step so the markdown->HTML conversion keeps the ordered list intact
(verified against python-markdown: the figure becomes part of the list item,
numbering does not restart). The hero image and the carousel/reel slides stay
separate — these step images reuse the carousel slides.
"""

from __future__ import annotations

import re

# A markdown numbered-list item: "1. ", "2. ", ... at the start of a line.
_STEP_RE = re.compile(r"^\d+\.\s")


def inject_step_images(
    body_markdown: str,
    image_urls: list[str],
    *,
    every_n: int = 2,
) -> str:
    """Insert an image under every ``every_n`` numbered step, in order.

    Returns the body unchanged when there are no images or ``every_n < 1``.
    Images are consumed in order and injection stops once they run out — so
    they land in the first numbered list (the instructions), not later ones.
    """
    if not image_urls or every_n < 1:
        return body_markdown
    out: list[str] = []
    step_no = 0
    img_idx = 0
    for line in body_markdown.split("\n"):
        out.append(line)
        if _STEP_RE.match(line):
            step_no += 1
            if step_no % every_n == 0 and img_idx < len(image_urls):
                url = image_urls[img_idx]
                out.append("")
                out.append(
                    f'    <figure><img src="{url}" '
                    f'alt="Step {step_no}" /></figure>'
                )
                out.append("")
                img_idx += 1
    return "\n".join(out)
