"""Sidebar header card: ASCII avatar of the currently-focused agent
(or the session as fallback) + session name with picker trigger."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from term.avatar import render


class SessionCard(Vertical):
    """ASCII portrait card.

    Avatar is keyed off the focused agent's display name when one is
    focused, so swapping focus between agents swaps the face. Falls
    back to the session name when no agent is focused (empty session,
    sidebar parked).
    """

    DEFAULT_CSS = """
    SessionCard {
        height: 8;
        padding: 0 1;
        background: $boost;
    }
    SessionCard > #card-art {
        color: $accent;
        height: 5;
    }
    SessionCard > #card-agent {
        color: $text;
        text-style: bold;
        height: 1;
    }
    SessionCard > #card-session {
        color: $text-muted;
        height: 1;
    }
    SessionCard:hover > #card-art {
        color: $text;
    }
    SessionCard:hover > #card-session {
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
        self._session_name = session_name
        self._agent_name: str | None = None
        self._mood = mood
        self._art = Static("", id="card-art")
        self._agent_label = Static("", id="card-agent")
        self._session_label = Static("", id="card-session")

    def compose(self) -> ComposeResult:
        yield self._art
        yield self._agent_label
        yield self._session_label

    def on_mount(self) -> None:
        self._refresh()

    def set_name(self, name: str) -> None:
        """Update the session label. The avatar may also change if no
        agent is currently focused."""
        self._session_name = name
        self._refresh()

    def set_focused_agent(self, agent_display_name: str | None) -> None:
        """Drive the avatar from this agent's name. Pass None to fall
        back to the session-level avatar (empty session)."""
        self._agent_name = agent_display_name
        self._refresh()

    def set_mood(self, mood: str) -> None:
        if mood == self._mood:
            return
        self._mood = mood
        self._refresh()

    def _refresh(self) -> None:
        # Empty card if nothing to show yet.
        if not self._session_name and not self._agent_name:
            self._art.update("")
            self._agent_label.update("")
            self._session_label.update("")
            return
        avatar_source = self._agent_name or self._session_name
        avatar = render(avatar_source, self._mood)
        self._art.update(avatar.art)
        if self._agent_name:
            self._agent_label.update(self._agent_name)
        else:
            self._agent_label.update("[dim italic]no agent focused[/]")
        if self._session_name:
            self._session_label.update(f"▸ {self._session_name} ▾")
        else:
            self._session_label.update("")

    def on_click(self, _event) -> None:
        self.post_message(self.PickerRequested())
