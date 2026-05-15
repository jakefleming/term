"""Spawn picker: modal for choosing agent + optional role + mode.

Open via F6, the "+ Add agent" sidebar entry, or `:spawn` with no args.
Returns a dict {"agent": str, "role": str | None, "mode": str} on submit,
or None on cancel.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RadioButton, RadioSet, Static


_BLANK_ROLE_ID = "__blank__"


class SpawnPickerScreen(ModalScreen[dict | None]):
    CSS = """
    SpawnPickerScreen { align: center middle; }
    SpawnPickerScreen > Vertical {
        width: 56;
        height: auto;
        background: $surface;
        border: heavy $primary;
        padding: 1 2;
    }
    SpawnPickerScreen .title {
        color: $text;
        text-style: bold;
        padding: 0 0 1 0;
    }
    SpawnPickerScreen .section {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    SpawnPickerScreen RadioSet {
        background: $surface;
        border: none;
        padding: 0;
    }
    SpawnPickerScreen #hint {
        color: $text-muted;
        text-align: center;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter",  "submit", "Spawn",  priority=True),
    ]

    def __init__(self, agents: list[str], roles: list[str]) -> None:
        super().__init__()
        self._agents = agents
        self._roles = roles

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add agent", classes="title")

            yield Static("Agent (CLI to run)", classes="section")
            with RadioSet(id="agent-set"):
                for i, name in enumerate(self._agents):
                    yield RadioButton(name, value=(i == 0))

            yield Static("Role (prompt template)", classes="section")
            with RadioSet(id="role-set"):
                yield RadioButton("blank — no prompt", value=True)
                for name in self._roles:
                    yield RadioButton(name)

            yield Static("Mode", classes="section")
            with RadioSet(id="mode-set"):
                yield RadioButton("persistent", value=True)
                yield RadioButton("one-shot")

            yield Static("↑/↓ navigate · Tab next section · Enter spawn · Esc cancel",
                         id="hint")

    def on_mount(self) -> None:
        self.query_one("#agent-set", RadioSet).focus()

    def action_submit(self) -> None:
        agent = self._pick("agent-set", self._agents)
        role_idx = self._pick_index("role-set")
        mode = self._pick("mode-set", ["persistent", "one-shot"])
        if not agent or not mode:
            return
        role: str | None
        if role_idx is None or role_idx == 0:
            role = None
        else:
            role = self._roles[role_idx - 1]
        self.dismiss({"agent": agent, "role": role, "mode": mode})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _pick_index(self, set_id: str) -> int | None:
        rs = self.query_one(f"#{set_id}", RadioSet)
        return rs.pressed_index if rs.pressed_index >= 0 else None

    def _pick(self, set_id: str, options: list[str]) -> str | None:
        idx = self._pick_index(set_id)
        if idx is None or idx < 0 or idx >= len(options):
            return None
        return options[idx]
