"""Sidebar header card: ASCII avatar + session name + mood indicator."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from term.avatar import render


class SessionCard(Vertical):
    """Multi-line ASCII portrait of the active session."""

    DEFAULT_CSS = """
    SessionCard {
        height: 7;
        padding: 0 1;
        background: $boost;
    }
    SessionCard > #card-art {
        color: $accent;
        height: 5;
    }
    SessionCard > #card-name {
        color: $text;
        text-style: bold;
        padding: 0 0 0 0;
    }
    SessionCard:hover > #card-art {
        color: $text;
    }
    SessionCard:hover > #card-name {
        color: $accent;
    }
    """

    class PickerRequested(Message):
        pass

    def __init__(
        self,
        session_name: str = "",
        mood: str = "neutral",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._name = session_name
        self._mood = mood
        self._art = Static("", id="card-art")
        self._label = Static("", id="card-name")

    def compose(self) -> ComposeResult:
        yield self._art
        yield self._label

    def on_mount(self) -> None:
        self._refresh()

    def set_name(self, name: str) -> None:
        self._name = name
        self._refresh()

    def set_mood(self, mood: str) -> None:
        if mood == self._mood:
            return
        self._mood = mood
        self._refresh()

    def _refresh(self) -> None:
        if not self._name:
            self._art.update("")
            self._label.update("")
            return
        avatar = render(self._name, self._mood)
        self._art.update(avatar.art)
        suffix = " ▾"
        self._label.update(f"▸ {self._name}{suffix}")

    def on_click(self, _event) -> None:
        self.post_message(self.PickerRequested())
