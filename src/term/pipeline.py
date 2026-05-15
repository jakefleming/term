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
    status: str = "idle"  # idle | running | ready | blocked | exited
    # `seen` flips to True after the node has been spawned at least once.
    # Used by :resume: a "seen" node has CLI history we can pick up; a
    # never-seen node has nothing to resume.
    seen: bool = False


class PipelineRun:
    """Owns the per-node runtime state for one pipeline instance.

    Pipelines are scoped to a Session — Session decides where each node's
    worktree lives and what its branch is named. PipelineRun starts empty;
    callers populate `.nodes` via `restore_pipeline` (from a saved session)
    or `seed_pipeline_from_config` (from pipeline.toml for a fresh session).
    """

    def __init__(self, config: Config, workspace: Workspace) -> None:
        self.config = config
        self.workspace = workspace
        self.nodes: list[NodeState] = []

    def reset(self) -> None:
        self.nodes = []

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
