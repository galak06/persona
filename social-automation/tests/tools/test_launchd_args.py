"""Tests for per-platform `args` injection in launchd ProgramArguments.

The IG/FB split runs `comment_approver.py`/`comment_poster.py --platform <p>`
as separate launchd jobs. The plist builder must forward those args to the
child while keeping the watchdog wrapper, and must pass a skill positional arg
to `claude /<skill>` invocations — without disturbing arg-less flows.
"""

from __future__ import annotations

from tools.launchd_plists import _program_arguments, default_plist_paths


def _flow(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"id": "x", "skill": "comment-composer"}
    base.update(overrides)
    return base


def test_script_args_forwarded_through_watchdog() -> None:
    paths = default_plist_paths()
    argv = _program_arguments(
        _flow(script="scripts/comment_poster.py", args=["--platform", "instagram"]),
        paths,
    )
    # Watchdog wrapper retained, child args before --timeout so the watchdog
    # parses --timeout and forwards --platform to the child.
    assert paths["watchdog_script"] in argv
    assert argv[-2:] == ["--timeout", paths["watchdog_timeout"]]
    assert "--platform" in argv and "instagram" in argv
    assert argv.index("instagram") < argv.index("--timeout")


def test_arg_less_script_unchanged() -> None:
    paths = default_plist_paths()
    argv = _program_arguments(_flow(script="scripts/fb_scan.py"), paths)
    assert argv == [
        paths["python"],
        paths["watchdog_script"],
        "scripts/fb_scan.py",
        "--timeout",
        paths["watchdog_timeout"],
    ]


def test_skill_arg_appended_to_slash_command() -> None:
    paths = default_plist_paths()
    argv = _program_arguments(
        _flow(script=None, skill="comment-composer", args=["instagram"]),
        paths,
    )
    assert argv == [
        paths["claude_cli"],
        "--dangerously-skip-permissions",
        "/comment-composer",
        "instagram",
    ]


def test_arg_less_skill_unchanged() -> None:
    paths = default_plist_paths()
    argv = _program_arguments(_flow(script=None, skill="site-analyzer"), paths)
    assert argv == [
        paths["claude_cli"],
        "--dangerously-skip-permissions",
        "/site-analyzer",
    ]
