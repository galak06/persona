#!/usr/bin/env python3
"""Persona — command line entry point.

    python cli.py demo                          # no API keys needed
    python cli.py generate --topic "..."        # uses your brand + LLM

`demo` renders the full pipeline offline so a fresh clone produces real
files before you have configured anything.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "app"))

DEMO_BRAND = REPO_ROOT / "examples" / "demo-brand" / "config.json"
DEMO_TOPIC = "why your cold brew tastes bitter"


def _resolve_brand_config(raw: str | None) -> Path:
    """Accept a config.json path or a brand directory containing one."""
    if not raw:
        return DEMO_BRAND
    p = Path(raw).expanduser().resolve()
    if p.is_dir():
        p = p / "config.json"
    if not p.is_file():
        raise SystemExit(f"error: no brand config at {p}")
    return p


def _report(result: Any, brand: Any, offline: bool) -> None:
    rel = result.out_dir
    print(f"\n  {brand.name} — {len(result.slide_paths)} slides")
    for p, prov in zip(result.slide_paths, result.providers, strict=True):
        print(f"    {p.name:14} {prov}")
    print(f"    {'caption.txt':14} {len(result.caption.split())} words")
    if result.reel_path:
        print(f"    {'reel.mp4':14} composed")
    elif result.reel_skipped_reason:
        print(f"    {'reel.mp4':14} skipped — {result.reel_skipped_reason}")
    print(f"\n  → {rel}")
    if offline:
        print(
            "\n  Rendered offline with placeholder art. Set GEMINI_API_KEY "
            "for real imagery and a written caption."
        )


def _cmd_generate(args: argparse.Namespace) -> int:
    from studio.brand import BrandProfile
    from studio.render import render_carousel

    config_path = _resolve_brand_config(args.brand)
    brand = BrandProfile.from_config(config_path)
    result = render_carousel(
        args.topic,
        brand,
        Path(args.out).expanduser(),
        offline=args.offline,
        make_reel=not args.no_reel,
    )
    _report(result, brand, offline="placeholder" in result.providers)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    args.topic = args.topic or DEMO_TOPIC
    args.brand = args.brand or str(DEMO_BRAND)
    args.offline = True
    return _cmd_generate(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="persona",
        description="Generate brand-voice carousels, captions, and reels.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show progress logs")
    sub = parser.add_subparsers(dest="command", required=True)

    # `-v` is declared on both the root parser and every subcommand so that
    # `cli.py -v demo` and `cli.py demo -v` both work — people type both.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="show progress logs")
    common.add_argument("--brand", help="brand directory or config.json (default: demo brand)")
    common.add_argument("--out", default="out", help="output directory (default: ./out)")
    common.add_argument("--no-reel", action="store_true", help="skip MP4 composition")

    d = sub.add_parser("demo", parents=[common], help="render a sample carousel, no API keys")
    d.add_argument("--topic", help=f"override the demo topic (default: {DEMO_TOPIC!r})")
    d.set_defaults(func=_cmd_demo, offline=True)

    g = sub.add_parser("generate", parents=[common], help="render a carousel for your topic")
    g.add_argument("--topic", required=True, help="what the carousel is about")
    g.add_argument("--offline", action="store_true", help="skip all network calls")
    g.set_defaults(func=_cmd_generate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="  %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
