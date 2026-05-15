"""Spike app: a single PtyPane filling the screen.

This is the de-risking step in SPEC.md's build order — prove a child CLI
renders cleanly inside Textual before building anything pipeline-shaped.
"""

from __future__ import annotations

import os
from typing import Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding

from term.widgets.pty_pane import PtyPane


class SpikeApp(App[None]):
    """One pane, one child process. The smallest useful thing."""

    CSS = """
    Screen {
        background: $surface;
    }
    PtyPane {
        width: 100%;
        height: 100%;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, command: Sequence[str], cwd: str | None = None) -> None:
        super().__init__()
        self._command = list(command)
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        yield PtyPane(self._command, cwd=self._cwd, id="pane")

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


def run(command: Sequence[str] | None, cwd: str | None = None) -> None:
    cmd = list(command) if command else [os.environ.get("SHELL", "/bin/bash")]
    SpikeApp(cmd, cwd=cwd).run()
