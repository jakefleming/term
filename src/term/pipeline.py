"""Runtime state for a pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from term.config import Config, NodeSpec
from term.workspace import Workspace, Worktree


@dataclass
class NodeState:
    spec: NodeSpec
    worktree: Worktree
    last_handoff_commit: str | None = None
    status: str = "idle"  # idle | running | ready | blocked


class PipelineRun:
    """Owns the per-node runtime state for one pipeline instance."""

    def __init__(self, config: Config, workspace: Workspace) -> None:
        self.config = config
        self.workspace = workspace
        self.nodes: list[NodeState] = []

    def initialize(self) -> None:
        """Ensure a worktree exists for every node in the pipeline."""
        for spec in self.config.pipeline.nodes:
            wt = self.workspace.ensure_worktree(spec.id)
            self.nodes.append(NodeState(spec=spec, worktree=wt))

    def node(self, node_id: str) -> NodeState:
        for n in self.nodes:
            if n.spec.id == node_id:
                return n
        raise KeyError(node_id)

    def next_after(self, node_id: str) -> NodeState | None:
        for i, n in enumerate(self.nodes):
            if n.spec.id == node_id and i + 1 < len(self.nodes):
                return self.nodes[i + 1]
        return None

    def prev_before(self, node_id: str) -> NodeState | None:
        for i, n in enumerate(self.nodes):
            if n.spec.id == node_id and i > 0:
                return self.nodes[i - 1]
        return None

    def __iter__(self) -> Iterable[NodeState]:
        return iter(self.nodes)
