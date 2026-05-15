"""CLI for term.

  term [PATH]     # open the TUI in PATH (defaults to current directory)
  term init [PATH]
                  # scaffold .term/ in PATH (creates a git repo if needed)
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


def _cmd_default(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve() if args.path else None
    if path is not None and not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2
    run_term(workspace_root=path, yolo=args.yolo, mouse=args.mouse)
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
    raw = sys.argv[1:] if argv is None else list(argv)

    # Hand-dispatch subcommands so the default `term [PATH]` form doesn't
    # collide with argparse's subparser positional handling.
    if raw and raw[0] == "init":
        parser = argparse.ArgumentParser(
            prog="term init",
            description="Scaffold a term workspace.",
        )
        parser.add_argument("path", nargs="?", default=None,
                            help="Directory to init (defaults to current dir)")
        ns = parser.parse_args(raw[1:])
        sys.exit(_cmd_init(ns))

    if raw and raw[0] == "spike":
        parser = argparse.ArgumentParser(
            prog="term spike",
            description="One-pane PTY widget smoke test.",
        )
        parser.add_argument("command", nargs=argparse.REMAINDER,
                            help="Command to run inside the pane (default: $SHELL)")
        parser.add_argument("--cwd", default=None)
        ns = parser.parse_args(raw[1:])
        sys.exit(_cmd_spike(ns))

    # Default: `term [PATH]`.
    parser = argparse.ArgumentParser(
        prog="term",
        description="TUI for orchestrating CLI coding agents as a pipeline.",
        epilog="Subcommands: `term init [PATH]`, `term spike -- CMD`.",
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="Directory to open (defaults to current directory)")
    parser.add_argument(
        "--yolo", "--dangerously-skip-permissions",
        action="store_true", dest="yolo",
        help="Append each agent's yolo_args (e.g. claude's "
             "--dangerously-skip-permissions). Per-agent yolo_args live in "
             "bundled defaults or ~/.config/term/config.toml.",
    )
    mouse_group = parser.add_mutually_exclusive_group()
    mouse_group.add_argument(
        "--mouse", dest="mouse", action="store_true", default=None,
        help="Enable mouse tracking (clickable sidebar). Off by default on "
             "Apple Terminal because it breaks drag-and-drop of files; on "
             "elsewhere.",
    )
    mouse_group.add_argument(
        "--no-mouse", dest="mouse", action="store_false",
        help="Disable mouse tracking. Use this if drag-and-drop of files "
             "into agent panes isn't working in your terminal.",
    )
    ns = parser.parse_args(raw)
    sys.exit(_cmd_default(ns))


if __name__ == "__main__":
    main()
