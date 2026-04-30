"""Generate the Pinterest Standard Access demo video.

Pinterest's upgrade form requires a video showing:
  1. How the app authenticates Pinterest users
  2. The main Pinterest features the app uses

This script renders a 6-slide, ~85-second slideshow that covers both — no
screen recording needed. Output: ~/Desktop/dogfoodandfun-pinterest-demo.mp4.

Requires: Pillow + ffmpeg (both already installed on this machine).
Run:  python3 social-automation/scripts/make_pinterest_demo_video.py
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT = Path.home() / "Desktop" / "dogfoodandfun-pinterest-demo.mp4"
W, H = 1920, 1080

BG = (250, 249, 246)
FG = (25, 25, 25)
ACCENT = (230, 0, 35)
MUTED = (110, 110, 110)

FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"

SLIDE_SEC = 14
FADE_SEC = 0.4

SLIDES: list[dict] = [
    {
        "kind": "title",
        "title": "dogfoodandfun-publisher",
        "subtitle": "Pinterest API v5 integration",
        "footer": "App ID: 1564031  ·  Requesting Standard Access",
    },
    {
        "kind": "bullets",
        "title": "How the app authenticates",
        "body": [
            "OAuth 2.0 with Pinterest-hosted consent screen",
            "Only authenticated account: our own Pinterest business profile",
            "       pinterest.com/dogfoodandfun/",
            "No end users — this is a server-side publisher, no UI",
            "Tokens stored locally in a project-scoped config file",
        ],
    },
    {
        "kind": "scopes",
        "title": "Requested scopes",
        "rows": [
            ("pins:write", "publish 4 branded Pins per recipe"),
            ("pins:read", "dedupe before create"),
            ("boards:write", "maintain our own board metadata"),
            ("boards:read", "look up our own boards by ID"),
            ("user_accounts:read", "confirm the authenticated account"),
        ],
    },
    {
        "kind": "flow",
        "title": "What the app publishes",
        "flow": [
            "New recipe on dogfoodandfun.com",
            "↓",
            "4 branded Pins (one per recipe carousel slide)",
            "↓",
            "pinterest.com/dogfoodandfun/homemade-dog-recipes/",
        ],
        "footer": "Every Pin links back to the source recipe on our site.",
    },
    {
        "kind": "mono",
        "title": "Destination",
        "body": [
            "Profile:          pinterest.com/dogfoodandfun/",
            "Board:            Homemade Dog Recipes",
            "Domain verified:  dogfoodandfun.com  (meta p:domain_verify)",
            "Account type:     Business",
            "Privacy policy:   dogfoodandfun.com/privacy-policy/",
        ],
    },
    {
        "kind": "bullets",
        "title": "First-party content, first-party destination",
        "body": [
            "Publishes only our own recipe content",
            "Writes only to our own board",
            "No scraping of Pinterest data",
            "No third-party data resale",
            "No access to or storage of other Pinterest users' data",
        ],
        "footer": "Requesting Standard access to enable live pin creation.",
    },
]


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def render_slide(i: int, spec: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([(120, 120), (220, 140)], fill=ACCENT)
    d.text((W - 260, 126), f"{i} / {len(SLIDES)}", fill=MUTED, font=_font(FONT_REG, 36))

    title_font = _font(FONT_BOLD, 88)
    body_font = _font(FONT_REG, 44)
    mono_font = _font(FONT_MONO, 40)
    caption = _font(FONT_REG, 34)

    d.text((120, 180), spec["title"], fill=FG, font=title_font)

    kind = spec["kind"]
    if kind == "title":
        d.text((120, 330), spec["subtitle"], fill=ACCENT, font=_font(FONT_BOLD, 54))
        d.text((120, H - 270), spec["footer"], fill=MUTED, font=body_font)
        d.text((120, H - 190), "dogfoodandfun.com", fill=FG, font=_font(FONT_BOLD, 46))

    elif kind in ("bullets", "flow"):
        items = spec.get("body") or spec.get("flow", [])
        y = 360
        for line in items:
            if line.strip() == "↓":
                d.text((180, y), line, fill=ACCENT, font=_font(FONT_BOLD, 52))
                y += 72
                continue
            prefix = "•  " if kind == "bullets" else "   "
            d.text((120, y), f"{prefix}{line}", fill=FG, font=body_font)
            y += 78
        if spec.get("footer"):
            d.text((120, H - 180), spec["footer"], fill=MUTED, font=caption)

    elif kind == "scopes":
        y = 370
        for scope, desc in spec["rows"]:
            d.text((120, y), scope, fill=ACCENT, font=mono_font)
            d.text((600, y), desc, fill=FG, font=body_font)
            y += 86

    elif kind == "mono":
        y = 380
        for line in spec["body"]:
            d.text((120, y), line, fill=FG, font=mono_font)
            y += 72

    d.text(
        (120, H - 90),
        "Pinterest Standard Access application  —  dogfoodandfun-publisher",
        fill=MUTED,
        font=_font(FONT_REG, 28),
    )

    return img


def encode_clip(png: Path, out: Path) -> None:
    fade_out_start = SLIDE_SEC - FADE_SEC
    vf = f"fade=t=in:st=0:d={FADE_SEC},fade=t=out:st={fade_out_start}:d={FADE_SEC}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-t",
            str(SLIDE_SEC),
            "-i",
            str(png),
            "-vf",
            vf,
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            str(out),
        ],
        check=True,
    )


def concat_clips(clips: list[Path], target: Path, tmp: Path) -> None:
    listing = tmp / "clips.txt"
    listing.write_text("\n".join(f"file '{c}'" for c in clips))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
    )


def build_video() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pindemo_") as tdstr:
        td = Path(tdstr)
        clips: list[Path] = []
        for i, spec in enumerate(SLIDES, start=1):
            png = td / f"slide_{i:02d}.png"
            render_slide(i, spec).save(png)
            mp4 = td / f"clip_{i:02d}.mp4"
            encode_clip(png, mp4)
            clips.append(mp4)
        concat_clips(clips, OUTPUT, td)
    return OUTPUT


if __name__ == "__main__":
    out = build_video()
    size_kb = out.stat().st_size // 1024
    print(f"✓ wrote {out}  ({size_kb} KB, {len(SLIDES) * SLIDE_SEC}s target)")
