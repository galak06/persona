"""Reel composition tests.

`_pad_to_reel` and input validation are covered with fast unit tests.
Live ffmpeg composition is gated behind `RUN_FFMPEG_TESTS=1` since it shells
out and takes a few seconds per run.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from generators import reel


def _square_png(size: int = 512, color: tuple[int, int, int] = (120, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def test_pad_to_reel_produces_9x16_frame() -> None:
    img = Image.new("RGB", (1080, 1080), (10, 20, 30))
    out = reel._pad_to_reel(img)
    assert out.size == (reel._REEL_W, reel._REEL_H)


def test_pad_to_reel_passthrough_on_exact_size() -> None:
    img = Image.new("RGB", (reel._REEL_W, reel._REEL_H), (0, 0, 0))
    assert reel._pad_to_reel(img) is img


def test_compose_reel_rejects_empty_slides(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        reel.compose_reel([], tmp_path / "out.mp4")


def test_compose_reel_rejects_transition_longer_than_slide(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        reel.compose_reel(
            [_square_png()],
            tmp_path / "out.mp4",
            slide_duration_s=0.5,
            transition_duration_s=1.0,
        )


def test_compose_reel_raises_when_ffmpeg_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reel.shutil, "which", lambda _: None)
    with pytest.raises(reel.ReelCompositionError, match="ffmpeg"):
        reel.compose_reel([_square_png()], tmp_path / "out.mp4")


@pytest.mark.skipif(
    os.environ.get("RUN_FFMPEG_TESTS") != "1",
    reason="live ffmpeg test — set RUN_FFMPEG_TESTS=1 to enable",
)
def test_compose_reel_produces_playable_mp4(tmp_path: Path) -> None:
    colors = [(200, 80, 40), (40, 120, 80), (80, 40, 160), (220, 180, 60)]
    slides = [_square_png(color=c) for c in colors]
    out = tmp_path / "reel.mp4"
    reel.compose_reel(
        slides,
        out,
        slide_duration_s=2.0,
        transition_duration_s=0.3,
    )
    assert out.exists() and out.stat().st_size > 1024
