"""Reel narration: build a short script from the recipe and render it to audio.

Pulls the hook + 3 bullet facts out of `recipe.ig_caption` (which the voice
step already validated), appends a short site CTA, and renders to aiff via
macOS `say`. The aiff is passed to `compose_reel(audio_path=…)` which
ffmpeg-mixes it onto the video track.

macOS-only today. For a non-Mac target or better voice quality, swap
`synthesize_narration` for a Gemini TTS or ElevenLabs call — same
(text, output_path) signature.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = "Samantha"
_DEFAULT_RATE = 155  # words per minute — Reel-paced, slightly faster than conversational


class NarrationError(RuntimeError):
    """Raised when TTS synthesis fails."""


def build_narration_script(ig_caption: str) -> str:
    """Pull hook + bullet facts from a compliant ig_caption and add a site CTA.

    Assumes the caption follows the pipeline's structure: first non-bullet,
    non-hashtag line is the hook; three lines starting with `•` are the facts.
    """
    lines = [line.strip() for line in ig_caption.split("\n")]

    hook = next(
        (
            line
            for line in lines
            if line and not line.startswith("\u2022") and not line.startswith("#")
        ),
        "",
    )
    bullets = [
        line.lstrip("\u2022").strip() for line in lines if line.startswith("\u2022")
    ][:3]

    parts: list[str] = []
    if hook:
        parts.append(hook.rstrip("."))
    parts.extend(b.rstrip(".") for b in bullets if b)
    parts.append("Full recipe at persona dot com")

    return ". ".join(parts) + "."


def synthesize_narration(
    text: str,
    output_path: Path,
    *,
    voice: str = _DEFAULT_VOICE,
    rate: int = _DEFAULT_RATE,
) -> Path:
    """Render `text` to an aiff file via macOS `say`. Returns `output_path`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["say", "-v", voice, "-r", str(rate), "-o", str(output_path), text]
    logger.info("say voice=%s rate=%d out=%s", voice, rate, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise NarrationError(
            f"say failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return output_path
