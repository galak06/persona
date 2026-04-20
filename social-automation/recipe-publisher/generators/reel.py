"""Reel video composition: carousel slides -> 9:16 mp4 for IG Reels.

Takes slide image bytes (text overlays already baked in by text_overlay.py),
pads each to 9:16 with a blurred background fill, and composes an H.264 mp4
slideshow with fade transitions between slides. Output is suitable for upload
to IG via the Reels Graph API endpoint.

v1 emits a silent stereo AAC track alongside the video — IG Reels expects an
audio stream in the container even when there is no music. `audio_path` is the
seam for later music integration.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

_REEL_W = 1080
_REEL_H = 1920
_FPS = 30
_BLUR_RADIUS = 40


class ReelCompositionError(RuntimeError):
    """Raised when ffmpeg fails or prerequisites are missing."""


def compose_reel(
    slide_bytes: list[bytes],
    output_path: Path,
    *,
    slide_duration_s: float = 6.0,
    transition_duration_s: float = 0.5,
    audio_path: Path | None = None,
) -> Path:
    """Compose slide bytes into a 9:16 Reel mp4 and return the output path.

    Total duration: n * slide_duration_s - (n - 1) * transition_duration_s.
    With 4 slides at 6s each and 0.5s crossfades that is 22.5s — inside IG's
    3s-to-90s Reel window.
    """
    if not slide_bytes:
        raise ValueError("need at least one slide")
    if slide_duration_s <= transition_duration_s:
        raise ValueError("slide_duration_s must exceed transition_duration_s")
    if shutil.which("ffmpeg") is None:
        raise ReelCompositionError("ffmpeg not found on PATH")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / f".reel-{output_path.stem}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        frames = [
            _write_reel_frame(b, tmp_dir / f"frame_{i:02d}.png")
            for i, b in enumerate(slide_bytes)
        ]
        _run_ffmpeg(
            frames,
            output_path,
            slide_duration_s=slide_duration_s,
            transition_duration_s=transition_duration_s,
            audio_path=audio_path,
        )
        return output_path
    finally:
        for p in tmp_dir.glob("*"):
            p.unlink()
        tmp_dir.rmdir()


def _write_reel_frame(image_bytes: bytes, path: Path) -> Path:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    _pad_to_reel(img).save(path, "PNG")
    return path


def _pad_to_reel(img: Image.Image) -> Image.Image:
    """Return a 1080x1920 frame.

    Strategy depends on the input aspect:
    - Exact 1080x1920 → passthrough.
    - Near 9:16 (within 5%) → resize-to-fill, no blur-fill needed. Model outputs
      at 9:16 rarely land on exact 1080x1920; we stretch them in rather than
      painting blurred bands the user didn't ask for.
    - Otherwise (e.g. 1:1 carousel) → center + blurred zoomed-copy letterbox.
    """
    if img.width == _REEL_W and img.height == _REEL_H:
        return img

    target_ratio = _REEL_W / _REEL_H
    actual_ratio = img.width / img.height
    if abs(actual_ratio - target_ratio) / target_ratio < 0.05:
        return img.resize((_REEL_W, _REEL_H), Image.LANCZOS)

    bg_scale = max(_REEL_W / img.width, _REEL_H / img.height) * 1.2
    bg_w = int(img.width * bg_scale)
    bg_h = int(img.height * bg_scale)
    bg = img.resize((bg_w, bg_h), Image.LANCZOS)
    left = (bg_w - _REEL_W) // 2
    top = (bg_h - _REEL_H) // 2
    bg = bg.crop((left, top, left + _REEL_W, top + _REEL_H))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=_BLUR_RADIUS))

    fg_w, fg_h = _fit_within(img.width, img.height, _REEL_W, _REEL_H)
    fg = img.resize((fg_w, fg_h), Image.LANCZOS)
    bg.paste(fg, ((_REEL_W - fg_w) // 2, (_REEL_H - fg_h) // 2))
    return bg


def _fit_within(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    scale = min(max_w / w, max_h / h)
    return int(w * scale), int(h * scale)


def _run_ffmpeg(
    frame_paths: list[Path],
    output_path: Path,
    *,
    slide_duration_s: float,
    transition_duration_s: float,
    audio_path: Path | None,
) -> None:
    n = len(frame_paths)
    total_s = n * slide_duration_s - (n - 1) * transition_duration_s

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in frame_paths:
        cmd += [
            "-framerate", str(_FPS),
            "-loop", "1",
            "-t", f"{slide_duration_s}",
            "-i", str(p),
        ]
    if audio_path is not None:
        cmd += ["-i", str(audio_path)]
    else:
        cmd += [
            "-f", "lavfi",
            "-t", f"{total_s}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
    audio_idx = n

    filter_parts = [
        f"[{i}:v]setsar=1,format=yuv420p[v{i}]" for i in range(n)
    ]
    # Pad audio with silence if shorter, trim if longer — so the output
    # always matches the composed video length regardless of narration runtime.
    if audio_path is not None:
        filter_parts.append(
            f"[{audio_idx}:a]apad=whole_dur={total_s},atrim=0:{total_s},"
            f"asetpts=PTS-STARTPTS[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{audio_idx}:a"
    if n == 1:
        filter_parts.append("[v0]copy[vout]")
    else:
        prev = "v0"
        for i in range(1, n):
            offset = i * (slide_duration_s - transition_duration_s)
            out = "vout" if i == n - 1 else f"vx{i}"
            filter_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:"
                f"duration={transition_duration_s}:offset={offset}[{out}]"
            )
            prev = out

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-r", str(_FPS),
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(
        "ffmpeg compose reel slides=%d total=%.1fs out=%s",
        n, total_s, output_path,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReelCompositionError(
            f"ffmpeg failed (rc={result.returncode}): {result.stderr.strip()}"
        )
