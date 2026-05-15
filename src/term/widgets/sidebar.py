"""Pipeline sidebar: vertical list of nodes with status markers."""

from __future__ import annotations

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
    """Shows the pipeline. Selecting a node posts NodeSelected."""

    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        background: $boost;
        border-right: solid $primary;
        padding: 0 1;
    }
    Sidebar > #sidebar-title {
        color: $text-muted;
        text-style: bold;
        padding: 1 0;
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
    """

    class NodeSelected(Message):
        def __init__(self, node_id: str) -> None:
            super().__init__()
            self.node_id = node_id

    def __init__(self, pipeline: PipelineRun, id: str | None = None) -> None:
        super().__init__(id=id)
        self.pipeline = pipeline

    def compose(self) -> ComposeResult:
        yield Static(f"▸ {self.pipeline.config.pipeline.name}", id="sidebar-title")
        yield ListView(id="node-list")

    async def on_mount(self) -> None:
        await self.refresh_nodes()

    async def refresh_nodes(self) -> None:
        lv = self.query_one("#node-list", ListView)
        await lv.clear()
        for node in self.pipeline.nodes:
            mark = _STATUS_MARK.get(node.status, "?")
            role = self.pipeline.config.roles[node.spec.role]
            agent = role.agent
            mode_glyph = "▶" if node.spec.mode == "persistent" else "⚡"
            label = f"{mark} {mode_glyph} {node.spec.id}\n    {agent}"
            await lv.append(ListItem(Label(label)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index or 0
        if 0 <= index < len(self.pipeline.nodes):
            self.post_message(
                self.NodeSelected(self.pipeline.nodes[index].spec.id)
            )

    def focus_list(self) -> None:
        self.query_one("#node-list", ListView).focus()
