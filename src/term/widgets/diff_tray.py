"""Diff tray: shows pending changes for the focused node's worktree.

Renders three sections:
  - working tree status (`git status --short`)
  - recent commit subjects (`git log --oneline -5`)
  - working tree diff (`git diff HEAD`), syntax-highlighted

The tray refreshes whenever the focused node changes or after a handoff.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from term.workspace import git


class DiffTray(VerticalScroll):
    DEFAULT_CSS = """
    DiffTray {
        width: 48;
        border-left: solid $primary;
        background: $boost;
        padding: 0 1;
    }
    DiffTray > #diff-title {
        color: $text-muted;
        text-style: bold;
        padding: 1 0;
    }
    DiffTray > #diff-body {
        padding: 0 0 1 0;
    }
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._title = Static("▸ diff", id="diff-title")
        self._body = Static("", id="diff-body")
        self._current_label: str = ""
        self._current_path: Path | None = None

    def compose(self) -> ComposeResult:
        yield self._title
        yield self._body

    def show_for(self, label: str, worktree: Path) -> None:
        self._current_label = label
        self._current_path = worktree
        self._title.update(f"▸ {label}")
        status = _safe_git(["status", "--short"], cwd=worktree)
        recent = _safe_git(["log", "--oneline", "-5"], cwd=worktree)
        diff = _safe_git(["diff", "HEAD", "--no-color"], cwd=worktree)
        self._body.update(_render(status, recent, diff))

    def refresh_current(self) -> None:
        if self._current_path is not None:
            self.show_for(self._current_label, self._current_path)


def _safe_git(args: list[str], *, cwd: Path) -> str:
    try:
        cp = git(args, cwd=cwd, check=False)
        return cp.stdout.decode(errors="replace")
    except Exception as e:
        return f"<git error: {e}>"


def _render(status: str, recent: str, diff: str) -> Group:
    parts: list = []
    if status.strip():
        parts.append(Text("Working tree", style="bold"))
        parts.append(Text(status.rstrip()))
        parts.append(Text(""))
    else:
        parts.append(Text("Working tree: clean", style="dim"))
        parts.append(Text(""))

    if recent.strip():
        parts.append(Text("Recent commits", style="bold"))
        parts.append(Text(recent.rstrip(), style="dim"))
        parts.append(Text(""))

    if diff.strip():
        parts.append(Text("Diff (uncommitted)", style="bold"))
        parts.append(Syntax(
            diff, "diff",
            background_color="default",
            word_wrap=True,
            line_numbers=False,
        ))
    else:
        parts.append(Text("No uncommitted changes", style="dim"))

    return Group(*parts)
