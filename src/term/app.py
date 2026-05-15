"""Textual apps for term.

Two apps live here:

- `TermApp` — the real thing. Loads a pipeline, ensures a worktree per node,
  spawns one persistent PtyPane per node, shows a sidebar to switch focus,
  and lets you hand off the focused node to the next with F2.
- `SpikeApp` — the single-pane smoke-test app used to de-risk the PTY widget.
  Kept around because `term spike -- <cmd>` is useful for poking at the
  rendering layer in isolation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer

from term.config import Config
from term.handoff import handoff as do_handoff
from term.pipeline import PipelineRun
from term.widgets.pty_pane import PtyPane
from term.widgets.sidebar import Sidebar
from term.workspace import Workspace


class SpikeApp(App[None]):
    """One pane, one child process. The smallest useful thing."""

    CSS = """
    Screen { background: $surface; }
    PtyPane { width: 100%; height: 100%; }
    """

    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, command: Sequence[str], cwd: str | None = None) -> None:
        super().__init__()
        self._command = list(command)
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        yield PtyPane(self._command, cwd=self._cwd, id="pane")

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


class TermApp(App[None]):
    """Pipeline + per-node PTY panes + handoff."""

    CSS = """
    Screen { background: $surface; layout: vertical; }
    #body { height: 1fr; layout: horizontal; }
    PtyPane { width: 100%; height: 100%; }
    ContentSwitcher#panes { width: 1fr; height: 100%; background: $surface; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("f2", "handoff", "Handoff →", priority=True),
        Binding("f5", "toggle_sidebar_focus", "Pipeline", priority=True),
    ]

    def __init__(self, config: Config, workspace: Workspace) -> None:
        super().__init__()
        self.config = config
        self.workspace = workspace
        self.pipeline = PipelineRun(config, workspace)
        self._current_node_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Sidebar(self.pipeline, id="sidebar")
            yield ContentSwitcher(id="panes")
        yield Footer()

    async def on_mount(self) -> None:
        # Build worktrees (may take a moment first time).
        self.pipeline.initialize()

        switcher = self.query_one("#panes", ContentSwitcher)
        for node in self.pipeline.nodes:
            if node.spec.mode != "persistent":
                continue  # one-shot nodes don't get a pane
            recipe = self.config.agent_for_role(node.spec.role)
            pane = PtyPane(
                recipe.command,
                cwd=str(node.worktree.path),
                id=self._pane_id(node.spec.id),
            )
            await switcher.mount(pane)

        await self.query_one(Sidebar).refresh_nodes()

        first_persistent = next(
            (n for n in self.pipeline.nodes if n.spec.mode == "persistent"),
            None,
        )
        if first_persistent is not None:
            self._focus_node(first_persistent.spec.id)

    def _pane_id(self, node_id: str) -> str:
        # ContentSwitcher uses widget id; sanitize node id to be a valid id.
        return "pane-" + node_id.replace("/", "_")

    def _focus_node(self, node_id: str) -> None:
        try:
            node = self.pipeline.node(node_id)
        except KeyError:
            return
        if node.spec.mode != "persistent":
            return
        switcher = self.query_one("#panes", ContentSwitcher)
        switcher.current = self._pane_id(node_id)
        self._current_node_id = node_id
        pane = self.query_one(f"#{self._pane_id(node_id)}", PtyPane)
        pane.focus()

    def on_sidebar_node_selected(self, message: Sidebar.NodeSelected) -> None:
        self._focus_node(message.node_id)

    def action_toggle_sidebar_focus(self) -> None:
        sidebar = self.query_one(Sidebar)
        if sidebar.has_focus_within and self._current_node_id is not None:
            self._focus_node(self._current_node_id)
        else:
            sidebar.focus_list()

    async def action_handoff(self) -> None:
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        source = self.pipeline.node(self._current_node_id)
        target = self.pipeline.next_after(source.spec.id)
        if target is None:
            self.notify(f"{source.spec.id} is the last node — nothing to hand off to",
                        severity="warning")
            return

        try:
            result = do_handoff(self.workspace, source, target)
        except Exception as e:
            self.notify(f"handoff failed: {e}", severity="error", timeout=8)
            return

        if not result.ok:
            self.notify(result.message, severity="warning", timeout=6)
            return

        self.notify(result.message, timeout=4)

        # Inject the target role's prompt template into its pane (if persistent).
        if target.spec.mode == "persistent":
            role = self.config.roles[target.spec.role]
            if role.prompt_template:
                pane = self.query_one(f"#{self._pane_id(target.spec.id)}", PtyPane)
                self._inject_prompt(pane, role.prompt_template)
            self._focus_node(target.spec.id)

        await self.query_one(Sidebar).refresh_nodes()

    def _inject_prompt(self, pane: PtyPane, prompt: str) -> None:
        """Write the role's prompt into the target pane's PTY and submit it."""
        if not pane.is_alive:
            return
        proc = pane._proc  # internal but stable enough for now
        if proc is None:
            return
        # Newlines in the middle of the prompt could submit early in some CLIs.
        # Single-line collapse keeps the injection robust; the agent still sees
        # the full instruction.
        line = " ".join(prompt.split())
        try:
            os.write(proc.fd, line.encode("utf-8"))
            os.write(proc.fd, b"\r")
        except OSError:
            pass


def run_term(workspace_root: Path | None = None) -> None:
    from term.config import load_config
    workspace = (
        Workspace(workspace_root) if workspace_root is not None
        else Workspace.discover(Path.cwd())
    )
    config = load_config(workspace.root)
    TermApp(config, workspace).run()


def run_spike(command: Sequence[str] | None, cwd: str | None = None) -> None:
    cmd = list(command) if command else [os.environ.get("SHELL", "/bin/bash")]
    SpikeApp(cmd, cwd=cwd).run()
