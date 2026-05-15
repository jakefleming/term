"""Modal for switching / creating / deleting workspace sessions."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static


@dataclass
class _SessionRow:
    id: str
    name: str
    node_count: int
    is_current: bool


class SessionPickerScreen(ModalScreen[dict | None]):
    """Returns one of:
        {"action": "switch", "id": <session_id>}
        {"action": "create", "name": <name>}
        {"action": "delete", "id": <session_id>}
        None  (cancelled)
    """

    CSS = """
    SessionPickerScreen { align: center middle; }
    SessionPickerScreen > Vertical {
        width: 56;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: heavy $primary;
        padding: 1 2;
    }
    SessionPickerScreen .title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    SessionPickerScreen ListView {
        height: auto;
        max-height: 14;
        background: $surface;
    }
    SessionPickerScreen ListItem {
        background: $surface;
        padding: 0 1;
    }
    SessionPickerScreen ListItem.--highlight {
        background: $primary 50%;
    }
    SessionPickerScreen #hint {
        color: $text-muted;
        padding: 1 0 0 0;
        text-align: center;
    }
    SessionPickerScreen #name-input {
        margin-top: 1;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel",  "Cancel", priority=True),
        Binding("n",      "new",     "New",    priority=True),
        Binding("d",      "delete",  "Delete", priority=True),
    ]

    def __init__(self, sessions: list[_SessionRow]) -> None:
        super().__init__()
        self._sessions = sessions
        self._creating = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Sessions", classes="title")
            yield ListView(id="session-list")
            yield Static(
                "↑↓ select · Enter switch · n new · d delete · esc cancel",
                id="hint",
            )

    async def on_mount(self) -> None:
        lv = self.query_one("#session-list", ListView)
        await lv.clear()
        cur_idx = 0
        for i, s in enumerate(self._sessions):
            mark = "◉" if s.is_current else "◯"
            label = f"{mark} {s.name}   ({s.node_count} agent{'s' if s.node_count != 1 else ''})"
            await lv.append(ListItem(Label(label)))
            if s.is_current:
                cur_idx = i
        lv.index = cur_idx
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or not (0 <= idx < len(self._sessions)):
            return
        self.dismiss({"action": "switch", "id": self._sessions[idx].id})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_new(self) -> None:
        if self._creating:
            return
        self._creating = True
        vbox = self.query_one(Vertical)
        # Add a name-input below the list.
        inp = Input(placeholder="new session name…", id="name-input")
        self.call_after_refresh(self._show_input, vbox, inp)

    async def _show_input(self, vbox: Vertical, inp: Input) -> None:
        await vbox.mount(inp)
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            self.dismiss(None)
            return
        self.dismiss({"action": "create", "name": name})

    def action_delete(self) -> None:
        lv = self.query_one("#session-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._sessions)):
            return
        s = self._sessions[idx]
        if s.is_current:
            self.app.notify(
                "switch to another session before deleting this one",
                severity="warning",
            )
            return
        self.dismiss({"action": "delete", "id": s.id})
