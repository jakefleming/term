"""Pipeline sidebar: vertical list of nodes plus a + Add affordance."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static

from term.pipeline import PipelineRun
from term.widgets.session_card import SessionCard


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
    Sidebar ListItem.tasks-header Label {
        color: $text-muted;
        text-style: italic;
    }
    Sidebar ListItem.task-row Label {
        color: $secondary;
    }
    """

    class NodeSelected(Message):
        def __init__(self, node_id: str) -> None:
            super().__init__()
            self.node_id = node_id

    class TaskSelected(Message):
        """Posted when the user activates a sub-task row in the tray."""

        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

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
        # Read on each refresh. Set by the app when sub-tasks are
        # active so we can show a small "Tasks" tray.
        self.subtasks: list = []

    def set_session_name(self, name: str) -> None:
        self._session_name = name
        try:
            self.query_one(SessionCard).set_name(name)
        except Exception:
            pass

    def set_session_mood(self, mood: str) -> None:
        try:
            self.query_one(SessionCard).set_mood(mood)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield SessionCard(self._session_name, id="session-card")
        yield ListView(id="node-list")

    def on_session_card_picker_requested(
        self, _msg: SessionCard.PickerRequested
    ) -> None:
        # Bubble the card's click as our own picker-requested event.
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
                display = node.spec.display
                id_suffix = f"" if display == node.spec.id else f"  ({node.spec.id})"
                label = (
                    f"{mark} {mode_glyph} {display}{id_suffix}{resume_tag}\n"
                    f"    {agent}  ·  {role}"
                )
                await lv.append(ListItem(Label(label)))
            # Tasks tray: in-flight sub-agent runs, if any. Track the
            # subtask id per row so a click on the row can be mapped
            # back to a SubTask without depending on the list index.
            live_tasks = [
                s for s in self.subtasks
                if s.status in {"queued", "running"}
            ]
            self._row_task_ids: list[str | None] = (
                [None] * len(self.pipeline.nodes)
            )
            if live_tasks:
                header = ListItem(Label(f"─ Tasks ({len(live_tasks)}) ─"))
                header.add_class("tasks-header")
                await lv.append(header)
                self._row_task_ids.append(None)
                for sub in live_tasks:
                    elapsed = int(sub.elapsed)
                    mm, ss = elapsed // 60, elapsed % 60
                    model_tag = f" {sub.model}" if sub.model else ""
                    state_dot = "●" if sub.status == "running" else "·"
                    text = (
                        f"  {state_dot} {sub.sender_id} → "
                        f"{sub.agent_name}{model_tag}\n"
                        f"      {sub.display}   {mm:d}:{ss:02d}"
                    )
                    row = ListItem(Label(text))
                    row.add_class("task-row")
                    await lv.append(row)
                    self._row_task_ids.append(sub.id)
            add_item = ListItem(Label("+ Add agent"))
            add_item.add_class("add-row")
            await lv.append(add_item)
            self._row_task_ids.append(None)  # add-agent row

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None:
            return
        n_nodes = len(self.pipeline.nodes)
        if index < n_nodes:
            self.post_message(
                self.NodeSelected(self.pipeline.nodes[index].spec.id)
            )
            return
        # Beyond the node rows: dispatch by the per-row table built in
        # refresh_nodes (handles task rows + add row + the inert
        # "Tasks" header in any order). Defensively bounds-check.
        row_ids = getattr(self, "_row_task_ids", None)
        if row_ids is not None and 0 <= index < len(row_ids):
            tid = row_ids[index]
            if tid is not None:
                self.post_message(self.TaskSelected(tid))
                return
        # Last row is "+ Add agent".
        list_view = event.list_view
        if index == len(list_view.children) - 1:
            self.post_message(self.AddRequested())

    def focus_list(self) -> None:
        self.query_one("#node-list", ListView).focus()
