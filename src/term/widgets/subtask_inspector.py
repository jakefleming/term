"""Modal inspector for an in-flight or recently-completed sub-task.

Opens when the user clicks a row in the sidebar's Tasks section. Shows:
  - header: who spawned it, which agent + model, status, elapsed
  - the prompt (collapsible-ish: scrollable)
  - the live stdout (auto-tails)

While the sub is alive, the screen polls the SubTask and appends new
output. Closes on Esc / q. Esc never leaks back to the underlying
pane because it's a ModalScreen.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

from term.subagent import SubTask


class SubTaskInspectorScreen(ModalScreen[None]):
    CSS = """
    SubTaskInspectorScreen {
        align: center middle;
    }
    SubTaskInspectorScreen > Vertical {
        width: 90%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: heavy $primary;
        padding: 1 2;
    }
    SubTaskInspectorScreen #subtask-header {
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }
    SubTaskInspectorScreen #subtask-meta {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    SubTaskInspectorScreen #subtask-prompt-label,
    SubTaskInspectorScreen #subtask-output-label {
        color: $text-muted;
        text-style: italic;
        padding: 1 0 0 0;
    }
    SubTaskInspectorScreen #subtask-prompt {
        height: 8;
        background: $surface-darken-1;
        padding: 0 1;
    }
    SubTaskInspectorScreen #subtask-output {
        height: 1fr;
        background: $surface-darken-1;
        padding: 0 1;
    }
    SubTaskInspectorScreen #subtask-footer {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape,q", "pop", "Close", priority=True),
    ]

    # Tail every 0.4s so the inspector feels live.
    _TICK_INTERVAL = 0.4

    def __init__(self, sub: SubTask) -> None:
        super().__init__()
        self._sub = sub
        self._output_pos = 0  # bytes-of-sub.output we've already written

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._header_text(), id="subtask-header")
            yield Static(self._meta_text(), id="subtask-meta")
            yield Static("Prompt", id="subtask-prompt-label")
            yield RichLog(
                id="subtask-prompt",
                markup=False,
                highlight=False,
                wrap=True,
            )
            yield Static("Output", id="subtask-output-label")
            yield RichLog(
                id="subtask-output",
                markup=False,
                highlight=False,
                wrap=True,
            )
            yield Static("[dim]esc / q to close[/dim]", id="subtask-footer")

    def on_mount(self) -> None:
        # Write the prompt once; it doesn't change.
        prompt_log = self.query_one("#subtask-prompt", RichLog)
        prompt_log.write(self._sub.prompt)
        # Seed any output we already have, then tail.
        self._tail_output()
        self.set_interval(self._TICK_INTERVAL, self._tick)

    def _tick(self) -> None:
        self.query_one("#subtask-header", Static).update(self._header_text())
        self.query_one("#subtask-meta", Static).update(self._meta_text())
        self._tail_output()

    def _tail_output(self) -> None:
        output = self._sub.output
        if len(output) > self._output_pos:
            new = output[self._output_pos:]
            self._output_pos = len(output)
            self.query_one("#subtask-output", RichLog).write(new)

    def _header_text(self) -> str:
        model = f" · {self._sub.model}" if self._sub.model else ""
        return (
            f"sub-task · {self._sub.sender_id} → "
            f"{self._sub.agent_name}{model}  ({self._sub.display})"
        )

    def _meta_text(self) -> str:
        elapsed = int(self._sub.elapsed)
        mm, ss = elapsed // 60, elapsed % 60
        rc = f"rc={self._sub.rc}" if self._sub.rc is not None else "—"
        return f"status: {self._sub.status}   elapsed: {mm:d}:{ss:02d}   {rc}"

    def action_pop(self) -> None:
        self.app.pop_screen()
