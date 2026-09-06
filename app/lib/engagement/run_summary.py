"""The one-line run summary both engagers finish with, plus its funnel.

`scripts/fb_engager.py` and `scripts/ig_engager.py` each built this string
inline, in two copies that differed only in the word for a source ("Groups" vs
"Hashtags"). They are one function now because the funnel had to be added to
both, and two copies of a funnel drift apart.

The funnel answers "where did the posts go?" without grepping. Every post
`posts_scanned` counted left through exactly one exit, in this order:

    scanned -> duplicates -> pre_filtered -> below_threshold -> candidates
                                                 -> drafted -> posted/declined

`extract_fail` and `empty_caption` sit beside those rather than in the chain:
they describe the SCRAPE that produced each post, not the gate it left through,
so a post can be both `extract_fail` and `duplicate`. They are the pair that
matters most here -- a run reporting `scanned=110 extract_fail=110
candidates=0` is a broken scraper, and a run reporting `scanned=110
extract_fail=0 candidates=0` is a quiet day on Instagram. Those two produced
byte-identical output before this module existed.

`skill_finished` puts the summary in front of a human (Telegram); `log_funnel`
puts the same numbers in the structured log for after the fact. Both, because
the Telegram message is not searchable and the log is not noticed.
"""

from __future__ import annotations

from lib.engagement.collaborators import Log
from lib.engagement.scan_results import ScanReport


def funnel_counts(report: ScanReport) -> dict[str, int]:
    """The funnel as an ordered name -> count mapping.

    `pre_filtered` collapses the adapter's per-reason breakdown to a total;
    the breakdown itself stays on the report for callers that want it.
    """
    return {
        "scanned": report.posts_scanned,
        "duplicates": report.duplicates,
        "pre_filtered": sum(report.pre_filtered.values()),
        "extract_fail": report.extraction_failed,
        "empty_caption": report.empty_caption,
        "below_threshold": report.scored_below_threshold,
        "candidates": report.candidates,
        "drafted": report.comments_attempted,
        "posted": report.comments_posted,
        "declined": report.comments_declined,
    }


def format_funnel(report: ScanReport) -> str:
    """Render the funnel as `name=count` pairs for the summary line."""
    return " ".join(f"{name}={count}" for name, count in funnel_counts(report).items())


def stopped_suffix(report: ScanReport) -> str:
    """The truncation notice, or "" when the pass ran to the end.

    Appended rather than woven into the head so a complete pass reads exactly
    as it always has -- and so a truncated one cannot be mistaken for it. A
    scan that stopped early otherwise reported the same shape as a scan that
    finished, which is how a week of SIGKILLed ig-engager runs looked like
    quiet days on Instagram.
    """
    if report.stopped_reason is None:
        return ""
    return (
        f" | STOPPED: {report.stopped_reason} "
        f"({report.sources_visited}/{report.sources_total} sources reached)"
    )


def build_summary(
    report: ScanReport,
    *,
    source_label: str,
    comment_quota: int,
    dry_run: bool,
) -> str:
    """Build the `skill_finished` summary for one engager run.

    `source_label` is the platform's word for a source -- "Groups" for
    Facebook, "Hashtags" for Instagram. The leading half is unchanged from what
    both scripts emitted before; the funnel is appended.
    """
    if dry_run:
        head = (
            f"DRY RUN (nothing liked, commented or recorded) | "
            f"{source_label}: {report.sources_visited} | "
            f"Would like: {report.likes_attempted} | "
            f"Would comment: {report.comments_attempted}/{comment_quota} | "
            f"Agent declined: {report.comments_declined}"
        )
    else:
        head = (
            f"{source_label}: {report.sources_visited} | "
            f"Liked: {report.likes_succeeded} | "
            f"Commented: {report.comments_posted}/{comment_quota} | "
            f"Agent declined: {report.comments_declined}"
        )
    return f"{head} | Funnel: {format_funnel(report)}{stopped_suffix(report)}"


def log_funnel(report: ScanReport, log: Log) -> None:
    """Emit the funnel as one structured log line.

    Event-name-first with printf args, like every other line the pipeline
    writes -- `lib.bootstrap.init_script` hands the flows a stdlib
    `logging.Logger`, which takes positional args rather than kwargs.
    """
    log.info(
        "scan_funnel platform=%s sources=%d %s%s",
        report.platform,
        report.sources_visited,
        format_funnel(report),
        _stopped_fields(report),
    )


def _stopped_fields(report: ScanReport) -> str:
    """`stopped_reason=... sources_total=...`, or "" for a complete pass.

    The grep-side twin of `stopped_suffix`: same fact, `key=value` shaped so
    it parses like the rest of the line.
    """
    if report.stopped_reason is None:
        return ""
    return f" stopped_reason={report.stopped_reason} sources_total={report.sources_total}"
