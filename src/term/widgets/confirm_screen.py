"""Generic yes/no confirmation modal.

Used for destructive actions (session delete, etc.). Returns True if
confirmed, False on cancel.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 56;
        height: auto;
        background: $surface;
        border: heavy $error;
        padding: 1 2;
    }
    ConfirmScreen .title {
        text-style: bold;
        color: $error;
        padding: 0 0 1 0;
    }
    ConfirmScreen .body {
        padding: 0 0 1 0;
    }
    ConfirmScreen .hint {
        color: $text-muted;
        text-align: center;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "no",  "Cancel",  priority=True),
        Binding("n",      "no",  "No",      priority=True),
        Binding("y",      "yes", "Yes",     priority=True),
        Binding("enter",  "yes", "Confirm", priority=True),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="title")
            yield Static(self._body, classes="body")
            yield Static(
                "Enter / y: confirm   ·   Esc / n: cancel",
                classes="hint",
            )

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
