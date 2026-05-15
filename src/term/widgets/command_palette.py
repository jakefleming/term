"""Modal command palette: a bottom-aligned `:` Input.

Supports the v1 commands:
  spawn <role>          — append a persistent node
  spawn -o <role>       — append a one-shot node
  handoff               — same as F2 (focused → next)
  handoff <node>        — reroute: focused → <node>
  edit                  — same as F3 ($EDITOR on pending files)
  rerun                 — re-trigger a one-shot node
  quit                  — exit
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class CommandPaletteScreen(ModalScreen[str]):
    """Pops up a single-line `:` input. Returns the entered command or ''."""

    CSS = """
    CommandPaletteScreen {
        align: center bottom;
    }
    CommandPaletteScreen > Vertical {
        width: 80%;
        max-width: 100;
        background: $surface;
        border: heavy $primary;
        padding: 0 1;
        height: auto;
    }
    CommandPaletteScreen Static#cp-hint {
        color: $text-muted;
        padding: 0 1;
    }
    CommandPaletteScreen Input {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "spawn <role> · spawn -o <role> · handoff [<node>] · edit · rerun · quit",
                id="cp-hint",
            )
            yield Input(placeholder=": ", id="cp-input")

    def on_mount(self) -> None:
        self.query_one("#cp-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss("")
