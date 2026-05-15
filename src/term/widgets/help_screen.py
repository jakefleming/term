"""Help overlay: keybindings + palette reference."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP = """\
[b]term[/b]  — TUI for orchestrating CLI coding agents as a pipeline

[b]Keys[/b]
  Ctrl-Q      Quit
  F1          Command palette
  F2          Handoff focused node → next
  F3          Edit pending files in $EDITOR
  F4          Toggle diff tray
  F5          Toggle focus: pipeline sidebar ↔ pane
  F12         This help screen

[b]Palette commands[/b]  (open with F1)
  spawn <role>            Append a persistent node
  spawn -o <role>         Append a one-shot node
  handoff                 Same as F2
  handoff <node>          Reroute: focused → <node>
  edit                    Same as F3
  rerun                   Re-trigger a one-shot node
  quit                    Exit

[b]Status markers[/b] in the sidebar
  ·   idle           ●   running         ✓   ready
  ✗   blocked        ⏹   child exited

[b]Concepts[/b]
  Each node owns a [i]git worktree[/i] under .term/worktrees/<node-id> on
  branch term/node/<node-id>. Handoff = snapshot source's tracked files
  into target via [code]git archive HEAD | tar[/code], commit in target,
  inject the target role's prompt template.

[dim]press esc, q, or F12 to close[/dim]
"""


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > VerticalScroll {
        width: 80%;
        max-width: 90;
        height: 80%;
        background: $surface;
        border: heavy $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape,q,f12,question_mark", "pop", "Close", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(_HELP)

    def action_pop(self) -> None:
        self.app.pop_screen()
