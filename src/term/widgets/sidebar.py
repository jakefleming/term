"""Pipeline sidebar: vertical list of nodes plus a + Add affordance."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static

from term.pipeline import PipelineRun


_STATUS_MARK = {
    "idle":    "·",
    "running": "●",
    "ready":   "✓",
    "blocked": "✗",
    "exited":  "⏹",
}


class Sidebar(Vertical):
    """Shows the pipeline plus an "+ Add agent" entry at the bottom."""

    DEFAULT_CSS = """
    Sidebar {
        width: 30;
        background: $boost;
        border-right: solid $primary;
        padding: 0 1;
    }
    Sidebar > #sidebar-title {
        color: $accent;
        text-style: bold;
        padding: 1 0;
    }
    Sidebar > #sidebar-title:hover {
        color: $text;
        background: $primary 20%;
    }
    Sidebar > ListView {
        height: 1fr;
        background: $boost;
    }
    Sidebar ListItem {
        background: $boost;
        padding: 0 1;
    }
    Sidebar ListItem.--highlight {
        background: $primary 50%;
    }
    Sidebar ListItem.add-row Label {
        color: $accent;
        text-style: bold;
    }
    """

    class NodeSelected(Message):
        def __init__(self, node_id: str) -> None:
            super().__init__()
            self.node_id = node_id

    class AddRequested(Message):
        """Posted when the user activates the '+ Add agent' row."""
        pass

    class SessionPickerRequested(Message):
        """Posted when the user clicks the session header at top of sidebar."""
        pass

    def __init__(
        self,
        pipeline: PipelineRun,
        session_name: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.pipeline = pipeline
        self._session_name = session_name
        self._refresh_lock: asyncio.Lock | None = None

    def set_session_name(self, name: str) -> None:
        self._session_name = name
        try:
            self.query_one("#sidebar-title", Static).update(
                f"▸ {name}   ▾"
            )
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        label = self._session_name or "session"
        yield Static(f"▸ {label}   ▾", id="sidebar-title")
        yield ListView(id="node-list")

    def on_click(self, event) -> None:
        # Click on the session header → open the picker.
        try:
            title = self.query_one("#sidebar-title", Static)
        except Exception:
            return
        if event.widget is title:
            self.post_message(self.SessionPickerRequested())

    async def on_mount(self) -> None:
        await self.refresh_nodes()

    async def refresh_nodes(self) -> None:
        # Serialize concurrent refreshes (status tick + handoff + spawn can
        # all hit us at once; without a lock the ListView clear/append
        # interleaves and trips DuplicateIds).
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            lv = self.query_one("#node-list", ListView)
            await lv.clear()
            config = self.pipeline.config
            for node in self.pipeline.nodes:
                mark = _STATUS_MARK.get(node.status, "?")
                mode_glyph = "▶" if node.spec.mode == "persistent" else "⚡"
                agent = node.spec.agent
                role = node.spec.role or "—"
                # Show ↻ if this node has saved conversation history we
                # could resume (seen before AND the agent supports it).
                recipe = config.agents.get(agent)
                resumable = (
                    node.seen
                    and recipe is not None
                    and bool(recipe.resume_args)
                    and node.spec.mode == "persistent"
                )
                resume_tag = "  ↻" if resumable else ""
                label = (
                    f"{mark} {mode_glyph} {node.spec.id}{resume_tag}\n"
                    f"    {agent}  ·  {role}"
                )
                await lv.append(ListItem(Label(label)))
            add_item = ListItem(Label("+ Add agent"))
            add_item.add_class("add-row")
            await lv.append(add_item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None:
            return
        if index >= len(self.pipeline.nodes):
            # The trailing "+ Add agent" row.
            self.post_message(self.AddRequested())
            return
        self.post_message(
            self.NodeSelected(self.pipeline.nodes[index].spec.id)
        )

    def focus_list(self) -> None:
        self.query_one("#node-list", ListView).focus()
