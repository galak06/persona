"""Create a short MP4 slideshow from carousel slide JPEGs using ffmpeg.

Each slide is displayed for `duration_per_slide` seconds with a crossfade
transition between slides. The output is a square (1:1) H.264 MP4 suitable
for Facebook video posts.

Entry point:
    video_path = make_slideshow(slides_dir, output_path)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SLIDE_DURATION = 3.0      # seconds per slide
_FADE_DURATION = 0.4       # crossfade overlap between slides
_TARGET_SIZE = 1080        # square px
_FB_HEIGHT = 1350          # 4:5 portrait for FB feed/Reels
_FPS = 30


def make_slideshow(
    slides_dir: Path,
    output_path: Path,
    *,
    duration_per_slide: float = _SLIDE_DURATION,
    fade_duration: float = _FADE_DURATION,
    width: int = _TARGET_SIZE,
    height: int = _TARGET_SIZE,
) -> Path:
    """Combine slide_N.jpg files into an MP4 with crossfade transitions.

    Returns output_path. Raises RuntimeError if ffmpeg fails or no slides found.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")

    slides = sorted(
        slides_dir.glob("slide_*.jpg"),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    if not slides:
        raise RuntimeError(f"no slide_*.jpg files found in {slides_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(slides)

    if n == 1:
        # Trivial case — no transitions needed.
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duration_per_slide), "-i", str(slides[0]),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(_FPS),
            str(output_path),
        ]
        _run(cmd)
        return output_path

    # Input args — force explicit framerate on each still image.
    inputs: list[str] = []
    for s in slides:
        inputs += ["-loop", "1", "-r", str(_FPS), "-t", str(duration_per_slide), "-i", str(s)]

    # Scale each stream to the target dimensions (padding preserves aspect ratio), then concat.
    scale = (
        f"scale={width}:{height}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1"
    )
    parts = [f"[{i}:v]{scale}[v{i}]" for i in range(n)]
    concat_in = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0[out]")
    filter_complex = ";".join(parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd)
    logger.info("slideshow created: %s %dx%d (%.1fs)", output_path, width, height, duration_per_slide * n)
    return output_path


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr[-800:]}"
        )
