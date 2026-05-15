"""CLI for term.

Three commands:

  term            # open the TUI in the current workspace
  term init       # scaffold .term/ in the current directory (creates a git
                  # repo if needed)
  term spike CMD  # one-pane fallback for poking at the PTY widget
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from term.app import run_spike, run_term
from term.workspace import Workspace


def _cmd_init(args: argparse.Namespace) -> int:
    ws = Workspace.init(Path(args.path or "."))
    pipeline_toml = ws.term_dir / "pipeline.toml"
    was_new = not pipeline_toml.exists()
    if was_new:
        pipeline_toml.write_text(_DEFAULT_PIPELINE_TOML)
    print(f"workspace ready: {ws.root}")
    print(f"  - .term/pipeline.toml   {'(created)' if was_new else '(exists)'}")
    print(f"  - .term/worktrees/      (gitignored)")
    print()
    print("next: `term` to open the TUI.")
    return 0


def _cmd_spike(args: argparse.Namespace) -> int:
    run_spike(args.command or None, cwd=args.cwd)
    return 0


def _cmd_default(_args: argparse.Namespace) -> int:
    run_term()
    return 0


_DEFAULT_PIPELINE_TOML = """\
# term project pipeline. Edit freely.
#
# Nodes have an `agent` (CLI to spawn) and an optional `role` (prompt
# template). Available agents + roles come from bundled defaults plus
# anything you add in ~/.config/term/config.toml.
#
# By default term opens with no nodes — press F6 (or click "+ Add agent")
# to spawn one. If you want a starter pipeline that materializes on launch,
# uncomment the example below.

[pipeline]
name = "default"
nodes = []

# Example starter pipeline:
#
# [pipeline]
# name = "spec-review-build"
# nodes = [
#   { agent = "claude-code", role = "spec-writer", mode = "persistent" },
#   { agent = "codex",       role = "reviewer",   mode = "persistent" },
#   { agent = "claude-code", role = "builder",    mode = "persistent" },
# ]
"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="term",
        description="TUI for orchestrating CLI coding agents as a pipeline.",
    )
    sub = parser.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="Scaffold a term workspace in the current directory")
    init.add_argument("path", nargs="?", default=None)
    init.set_defaults(func=_cmd_init)

    spike = sub.add_parser("spike", help="One-pane PTY widget smoke test")
    spike.add_argument("command", nargs=argparse.REMAINDER,
                       help="Command to run inside the pane (default: $SHELL)")
    spike.add_argument("--cwd", default=None)
    spike.set_defaults(func=_cmd_spike)

    parser.set_defaults(func=_cmd_default)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
