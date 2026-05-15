"""Display widget for one-shot nodes.

One-shot nodes don't host an interactive PTY; they run an agent in
`agent -p "<prompt>"` form when handed-off to, and stream stdout/stderr
into a scrollable log.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Static


class OneShotPanel(Vertical, can_focus=True):
    DEFAULT_CSS = """
    OneShotPanel {
        background: $surface;
        padding: 0 1;
    }
    OneShotPanel > #osp-header {
        color: $text-muted;
        padding: 1 0;
    }
    OneShotPanel > #osp-log {
        background: $surface-darken-1;
        height: 1fr;
    }
    """

    def __init__(self, node_id: str, role: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self._node_id = node_id
        self._role = role
        self._header = Static(self._make_header("idle"), id="osp-header")
        self._log = RichLog(id="osp-log", highlight=False, markup=False, wrap=True)

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._log

    def _make_header(self, state: str) -> str:
        return f"⚡ one-shot · {self._node_id}  [{self._role}]  state: {state}"

    def set_state(self, state: str) -> None:
        self._header.update(self._make_header(state))

    def begin_run(self, command: list[str]) -> None:
        self._log.clear()
        self._log.write(f"$ {' '.join(command)}\n")
        self.set_state("running")

    def append(self, text: str) -> None:
        self._log.write(text)

    def end_run(self, rc: int) -> None:
        self._log.write(f"\n[exit {rc}]\n")
        self.set_state("ready" if rc == 0 else "blocked")
