"""
Watchdog wrapper — runs a script with a stuck-process detector.
If the child produces no output for --timeout seconds (default 180),
takes a screenshot, sends a Telegram alert, kills the process,
and exits with code 1.

Usage:
    python scripts/run_with_watchdog.py scripts/fb_scan.py [--timeout 180] [--force]
    python scripts/run_with_watchdog.py scripts/ig_scan.py --timeout 120

Extra args after the script path are forwarded to the child.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from notifier import send, skill_error


def _take_screenshot() -> Path | None:
    """Try to capture a macOS screenshot for debugging."""
    shot_dir = PROJECT_ROOT / "logs" / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = shot_dir / f"watchdog_{ts}.png"
    try:
        subprocess.run(
            ["screencapture", "-x", str(path)],
            timeout=5,
            capture_output=True,
        )
        if path.exists() and path.stat().st_size > 0:
            return path
    except Exception:
        pass
    return None


def run_with_watchdog(
    script: str,
    extra_args: list[str],
    timeout_secs: int = 180,
) -> int:
    """Run script as subprocess, kill if no output for timeout_secs."""
    script_name = Path(script).stem
    cmd = [sys.executable, "-u", script, *extra_args]  # -u = unbuffered

    print(f"[watchdog] Starting: {' '.join(cmd)}", flush=True)
    print(f"[watchdog] Stuck timeout: {timeout_secs}s", flush=True)
    print("[watchdog] PID: launching...", flush=True)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        bufsize=1,  # line-buffered
        text=True,
    )

    print(f"[watchdog] Child PID: {proc.pid}", flush=True)

    last_output_time = time.monotonic()
    last_line = ""
    killed = False

    def reader():
        """Read child stdout line by line, print + track last output time."""
        nonlocal last_output_time, last_line
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line, flush=True)
            last_output_time = time.monotonic()
            last_line = line

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    # Monitor loop — check for stuck process
    while proc.poll() is None:
        time.sleep(5)
        silent_secs = time.monotonic() - last_output_time

        if silent_secs > timeout_secs:
            killed = True
            print(
                f"\n[watchdog] STUCK — no output for {int(silent_secs)}s (limit: {timeout_secs}s)",
                flush=True,
            )
            print(f"[watchdog] Last output: {last_line[:120]}", flush=True)

            # Screenshot
            shot = _take_screenshot()
            shot_msg = f"\nScreenshot: {shot}" if shot else ""

            # Telegram alert
            alert = (
                f"🐕 <b>{script_name}</b> is STUCK\n"
                f"No output for {int(silent_secs)}s\n"
                f"Last line: <code>{last_line[:150]}</code>\n"
                f"Killing process (PID {proc.pid})...{shot_msg}"
            )
            send(alert)
            skill_error(script_name, f"Stuck for {int(silent_secs)}s — killed by watchdog")

            # Kill the child process tree
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.kill()

            print("[watchdog] Process killed.", flush=True)
            break

    reader_thread.join(timeout=5)
    exit_code = proc.returncode or (1 if killed else 0)

    if killed:
        print("[watchdog] Exiting with code 1 (stuck process killed).", flush=True)
    else:
        print(f"[watchdog] Process exited normally (code {exit_code}).", flush=True)

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a script with stuck-process watchdog")
    parser.add_argument("script", help="Python script to run")
    parser.add_argument(
        "--timeout", type=int, default=180, help="Seconds of silence before killing (default: 180)"
    )
    # Capture any remaining args to forward to the child script
    args, extra = parser.parse_known_args()

    exit_code = run_with_watchdog(args.script, extra, args.timeout)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
