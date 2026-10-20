"""Project launcher: setup, diagnostics, and supervised startup."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HELPERS = Path(__file__).resolve().parent
ROOT = HELPERS.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper_scripts.launch_support import (
    check_environment,
    ensure_environment,
    environment_python,
    run_dashboard,
    start_dashboard,
)

APP = "YouTubeKnowledge"
MINIMUM = (3, 10)
PORT = 8765


def main():
    parser = argparse.ArgumentParser(description=f"{APP} setup and launcher")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check setup without installing or starting anything",
    )
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument(
        "--update", action="store_true", help="Explicitly upgrade dependencies"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("url", nargs="?")
    parser.add_argument("limit", nargs="?", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.limit < 0:
        parser.error("limit cannot be negative")
    if args.check:
        ready, reason = check_environment(
            environment_python(ROOT), ROOT / "requirements.txt", MINIMUM
        )
        print(f"{APP}: {'ready' if ready else reason}")
        return 0 if ready else 1
    try:
        python = ensure_environment(ROOT, MINIMUM, args.update)
        if args.setup_only:
            print(f"{APP} setup complete.")
            return 0
        os.chdir(ROOT)
        if args.url:
            return subprocess.run(
                [
                    str(python),
                    str(ROOT / "main.py"),
                    "--no-venv",
                    "--urls",
                    args.url,
                    "--limit",
                    str(args.limit),
                ],
                cwd=ROOT,
                check=False,
            ).returncode

        def arguments(port):
            return [
                str(ROOT / "main.py"),
                "--no-venv",
                "--no-browser",
                "--port",
                str(port),
            ]

        dashboard = start_dashboard(ROOT, python, APP, args.port, arguments)
        return run_dashboard(dashboard, args.no_browser)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        print(
            "Run with --check for diagnostics or --setup-only --update to repair dependencies.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nLauncher stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
