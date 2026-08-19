"""Full CrewAI content pipeline -- strategist -> writer -> validate -> draft.

Extends `scripts/crewai_content_writer.py`'s strategist/writer stages with
the two new gates that make this pipeline safe to run unattended (no
human-in-the-loop approval anywhere -- see `/Users/gilcohen/.claude/plans/
immutable-frolicking-mitten.md`): `lib.crew.validate.validate_draft`
(deterministic medical-claims scan + LLM quality-editor rubric, both
hard-fail) and, only if that passes, `lib.crew.draft.create_wp_draft`
(a real WordPress REST POST with `status: "draft"` hardcoded -- never
publish, no override).

`--dry-run` runs every real LLM stage (strategist, writer, quality editor)
but skips the actual WordPress POST -- this is the safe way to see the
validation verdict (including a reject) before trusting the pipeline with a
live POST. It is NOT a free preview: DeepSeek calls happen either way, same
caveat as `crewai_content_writer.py` and `crewai_content_scout.py`.

`--validate-only PATH --title "..."` skips idea selection/strategist/writer
entirely and re-runs just the validate step against an already-assembled
HTML file (e.g. a prior run's `--output`, or an older `crew_drafts/*.html`
file) -- useful for checking whether the quality-editor gate actually
catches a known-bad draft without regenerating it. Never POSTs to
WordPress, regardless of `--dry-run`.

Usage::

    python -m scripts.crewai_content_pipeline --dry-run
    python -m scripts.crewai_content_pipeline --idea-id <content_ideas id>
    python -m scripts.crewai_content_pipeline \\
        --validate-only brands/dogfoodandfun/state/crew_drafts/<id>.html \\
        --title "GPS Trackers Without the Monthly Bill in 2026: ..."
"""

from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import ideas_db
from lib.affiliate_resolver import AffiliateResolverError
from lib.crew.brand_identity import read_brand_identity
from lib.crew.draft import DraftCreationError, create_wp_draft
from lib.crew.reference_clauses import reference_clause
from lib.crew.reference_library import resolve_reference
from lib.crew.validate import ValidationResult, validate_draft
from lib.crew.wp_image import build_image_brief, generate_wp_image
from lib.crew.writer import (
    assemble_final_html,
    build_content_brief,
    select_idea,
    write_post_from_brief,
)
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger

logger = get_logger(__name__)

_REQUIRED_ENV_VARS = ("DEEPSEEK_API_KEY", "AMAZON_ASSOCIATES_TAG")
_WP_ENV_VARS = ("WP_URL", "WP_USER", "WP_APP_PASSWORD")


def _infer_brand_dir() -> Path:
    """Same convention as `scripts/crewai_content_writer.py::_infer_brand_dir`."""
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "BRAND_DIR not set and brands/ doesn't have exactly one brand folder -- pass --brand-dir"
    )


def _check_required_env(*, need_wp: bool) -> list[str]:
    """Missing keys, checked after env loading so a clear error prints before
    any CrewAI/network call. `need_wp` is False for `--dry-run` and
    `--validate-only`, which never POST to WordPress."""
    names = _REQUIRED_ENV_VARS + (_WP_ENV_VARS if need_wp else ())
    return [name for name in names if not os.environ.get(name, "").strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-dir", type=Path, default=None, help="brand folder (default: $BRAND_DIR)"
    )
    parser.add_argument(
        "--idea-id",
        type=str,
        default=None,
        help="target a specific content_ideas row (default: highest-scored status='publish' row)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to write the assembled HTML (default: <brand-dir>/state/crew_drafts/<idea-id>.html)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run strategist+writer+validate for real but skip the WordPress POST -- "
        "prints what WOULD be posted and the validation verdict",
    )
    parser.add_argument(
        "--validate-only",
        type=Path,
        default=None,
        metavar="HTML_PATH",
        dest="validate_only_html",
        help="skip strategist/writer and re-run just the validate step against an "
        "existing assembled-HTML file. Requires --title. Never POSTs to WordPress.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="post title -- required with --validate-only; ignored otherwise (the "
        "writer's own title is used)",
    )
    return parser.parse_args()


def _print_validation_result(result: ValidationResult) -> None:
    print(f"passed   : {result.passed}")
    print(f"score    : {result.quality_score if result.quality_score is not None else '(n/a)'}")
    if result.reasons:
        print("reasons  :")
        for reason in result.reasons:
            print(f"  - {reason}")


def _run_validate_only(brand_dir: Path, html_path: Path, title: str | None) -> int:
    if not title:
        print("ERROR: --validate-only requires --title", file=sys.stderr)
        return 1
    if not html_path.is_file():
        print(f"ERROR: no such file: {html_path}", file=sys.stderr)
        return 1
    missing = _check_required_env(need_wp=False)
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    body_html = html_path.read_text(encoding="utf-8")
    print(f"brand    : {brand_dir.name}")
    print(f"input    : {html_path}")
    print(f"title    : {title}")
    print("\nrunning quality-editor validation only (real DeepSeek call; no WordPress POST)...")
    result = validate_draft(brand_dir, title=title, body_html=body_html)
    _print_validation_result(result)
    return 0 if result.passed else 2


def _run_full_pipeline(brand_dir: Path, args: argparse.Namespace) -> int:
    print(f"brand    : {brand_dir.name}")
    print(f"idea-id  : {args.idea_id or '(auto: highest-scored status=publish)'}")
    print("selecting idea...")

    idea = select_idea(brand_dir, idea_id=args.idea_id)
    if idea is None:
        print(
            "\nno matching content_ideas row found (check status='publish' exists, or "
            "--idea-id is correct)"
        )
        return 0
    idea_id = str(idea.get("id"))
    print(f"idea     : [{idea_id}] {idea.get('topic')}")

    print("\nrunning strategist crew (real DeepSeek call)...")
    brief = build_content_brief(brand_dir, idea)
    if brief is None:
        print("\nstrategist produced no structured brief (see logs) -- aborting")
        ideas_db.update_status(idea_id, "write_failed", "strategist produced no structured brief")
        return 1
    print(f"brief    : {brief.suggested_title} ({len(brief.outline)} sections)")

    print("\nrunning writer crew (real DeepSeek call)...")
    # discover=True only here: the drafting path is the one place a post about
    # a topic nobody curated products for must still find something linkable.
    written_post = write_post_from_brief(brand_dir, brief, discover=True)
    if written_post is None:
        print("\nwriter produced no structured post (see logs) -- aborting")
        ideas_db.update_status(idea_id, "write_failed", "writer produced no structured post")
        return 1
    print(f"title    : {written_post.title}")
    print(f"words    : {written_post.word_count}")

    print("\ninjecting JSON-LD + resolving affiliate placeholders...")
    try:
        # drop_unknown_affiliates: the writer invents catalog keys despite the
        # prompt forbidding it, and this path has no human in the loop -- a
        # fail-closed resolve discarded the entire finished post over one bad
        # key (live: 'plentum-gut-health-powder', 2026-08-16). Dropping the
        # placeholder costs one link; the remaining AffiliateResolverError
        # cases (missing associates tag, missing FTC disclosure) still abort,
        # because those would produce a legally wrong post rather than a
        # thinner one.
        final_html = assemble_final_html(brand_dir, written_post, drop_unknown_affiliates=True)
    except AffiliateResolverError as exc:
        print(f"\nAFFILIATE RESOLUTION FAILED: {exc}", file=sys.stderr)
        print(
            "(brief and draft above are still valid -- only final HTML assembly failed)",
            file=sys.stderr,
        )
        # Terminal status, same as the strategist/writer aborts above: without
        # it this exit path left the idea parked at 'drafting', which both
        # `api.ideas_api._revert_stale_claim` and this worker's own
        # `_revert_if_still_drafting` bounce straight back to 'approved' --
        # so the next sweep re-drafted the same idea, hit the same unknown
        # key, and threw away another full draft, invisibly and forever.
        # Not on --dry-run: that mode never mutates idea lifecycle state.
        if not args.dry_run:
            ideas_db.update_status(idea_id, "write_failed", f"affiliate resolution failed: {exc}")
        return 1

    output_path = args.output or (brand_dir / "state" / "crew_drafts" / f"{idea_id}.html")
    if args.dry_run:
        print(f"\n[DRY RUN] would write {len(final_html)} chars to {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_html, encoding="utf-8")
        print(f"\nwrote {len(final_html)} chars to {output_path}")

    print("\nrunning validate step (medical-claims gate + quality-editor, real DeepSeek call)...")
    result = validate_draft(
        brand_dir, title=written_post.title, body_html=final_html, outline=brief.outline
    )
    _print_validation_result(result)

    if not result.passed:
        print("\nVALIDATION FAILED -- no WordPress draft created.")
        # Not on --dry-run: that mode is an explicitly side-effect-free
        # preview (see module docstring) and must never mutate the idea's
        # real lifecycle state.
        if not args.dry_run:
            # result.reasons is the only place the medical-claims term or the
            # editor's score ever appears; without it on the row the cause is
            # unrecoverable once the container log rotates.
            ideas_db.update_status(
                idea_id, "validation_failed", "; ".join(result.reasons) or "validation failed"
            )
        return 2

    if args.dry_run:
        print(
            "\n[DRY RUN] validation passed -- would POST a status=draft WordPress post "
            "now, but --dry-run skips the real POST."
        )
        return 0

    print("\ncreating WordPress draft (real POST, status=draft)...")
    identity = read_brand_identity(brand_dir)
    mascot_name = identity.mascot_name
    image_brief = build_image_brief(written_post.title, brief.mascot_angle)
    # The strategist tagged this post with the kind of scene its hero should
    # show; the library answers with the photo that matches. `""` and an
    # unrecognised label are passed through untouched -- the resolver falls
    # back to `general` and then to None, and logs the unmatched tag.
    #
    # None here is NOT the reels/social case: this hero is the image being
    # created, so there is no pre-existing picture to keep instead. It simply
    # generates text2image (`generate_wp_image` takes its Imagen tiers when
    # `reference_image_bytes` is None, and `_style_suffix` then emits no
    # reference clause) -- no non-uploaded image is used as an anchor, which
    # is all "uploads only" asks.
    reference = resolve_reference(brand_dir, brief.reference_category, seed=idea_id)
    reference_image_path = reference.path if reference is not None else None
    # What the model is TOLD the photo is: a library image of a product or a
    # location must not be introduced as the brand's mascot. Ignored
    # downstream when there is no reference at all.
    clause = reference_clause(
        reference, identity.mascot_name, identity.mascot_kind, identity.persona_name
    )
    if reference is not None:
        print(f"reference: {reference.path} [{reference.category}] mascot={reference.shows_mascot}")
    tag_names = list(
        dict.fromkeys(
            name for name in [brief.primary_keyword, *brief.secondary_keywords[:2]] if name
        )
    )
    try:
        draft_result = create_wp_draft(
            idea_id=idea_id,
            title=written_post.title,
            body_html=final_html,
            image_brief=image_brief,
            mascot_name=mascot_name,
            category_name=str(idea.get("category") or ""),
            tag_names=tag_names,
            reference_image_path=reference_image_path,
            # The clause rides in on `create_wp_draft`'s generator seam rather
            # than as one more pass-through parameter through `lib.crew.draft`.
            generate_image_fn=partial(generate_wp_image, reference_clause=clause),
        )
    except DraftCreationError as exc:
        print(f"\nWORDPRESS DRAFT CREATION FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"\nWordPress draft created: post_id={draft_result.post_id} url={draft_result.post_url}")
    print(
        f"content_ideas[{idea_id}] updated: status=wp_draft, "
        f"wp_post_id={draft_result.post_id}, wp_url={draft_result.post_url}"
    )
    return 0


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    config_path = brand_dir / "config.json"
    if not config_path.is_file():
        print(f"ERROR: no config.json under {brand_dir}", file=sys.stderr)
        return 1

    if args.validate_only_html is not None:
        return _run_validate_only(brand_dir, args.validate_only_html, args.title)

    missing = _check_required_env(need_wp=not args.dry_run)
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        print(
            "DEEPSEEK_API_KEY / AMAZON_ASSOCIATES_TAG / WP_URL / WP_USER / WP_APP_PASSWORD "
            "-- see app/CLAUDE.md 'Credentials'.",
            file=sys.stderr,
        )
        return 1

    return _run_full_pipeline(brand_dir, args)


if __name__ == "__main__":
    raise SystemExit(main())
