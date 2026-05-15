"""CLI entry point for the spike."""

from __future__ import annotations

import argparse

from term.app import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="term",
        description="term — PTY pane spike. Hosts one CLI in one pane.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run inside the pane (default: $SHELL).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the child process.",
    )
    args = parser.parse_args()
    command = args.command if args.command else None
    run(command, cwd=args.cwd)


if __name__ == "__main__":
    main()
