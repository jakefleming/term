"""Persist + restore the live pipeline shape to `.term/session.json`.

Conversation history (Claude / Codex's per-directory session storage) lives
outside term — we just preserve the *pipeline shape* (which nodes were
there, with which agent/role/mode, and which had been spawned). Restoring
re-creates the worktrees + panes; the user opt-ins to conversation
resumption with `:resume` once the pane is mounted.

Schema is intentionally forgiving: missing fields fall back to sensible
defaults so format evolution stays cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from term.config import NodeSpec
from term.pipeline import NodeState, PipelineRun
from term.workspace import Workspace


_VERSION = 1


@dataclass
class _SavedNode:
    id: str
    agent: str
    role: str | None
    mode: str
    seen: bool = False
    last_status: str = "idle"


class Session:
    """Read/write the `.term/session.json` manifest."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.path = workspace.term_dir / "session.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> list[_SavedNode] | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        nodes_raw = raw.get("nodes", [])
        result: list[_SavedNode] = []
        for n in nodes_raw:
            try:
                result.append(_SavedNode(
                    id=n["id"],
                    agent=n["agent"],
                    role=n.get("role"),
                    mode=n.get("mode", "persistent"),
                    seen=bool(n.get("seen", False)),
                    last_status=n.get("last_status", "idle"),
                ))
            except KeyError:
                continue
        return result

    def save(self, pipeline: PipelineRun, current_node_id: str | None) -> None:
        self.workspace.prepare()
        payload = {
            "version": _VERSION,
            "current_node_id": current_node_id,
            "nodes": [
                {
                    "id": n.spec.id,
                    "agent": n.spec.agent,
                    "role": n.spec.role,
                    "mode": n.spec.mode,
                    "seen": n.seen,
                    "last_status": n.status,
                }
                for n in pipeline.nodes
            ],
        }
        try:
            self.path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def restore_pipeline(
    pipeline: PipelineRun,
    saved: list[_SavedNode],
    config_role_names: set[str],
    config_agent_names: set[str],
) -> tuple[int, int]:
    """Populate `pipeline.nodes` from saved entries.

    Returns (restored_count, skipped_count). Entries whose agent or role
    no longer exists in the config are skipped (with skipped_count > 0
    so the caller can surface it).
    """
    restored = 0
    skipped = 0
    for sn in saved:
        if sn.agent not in config_agent_names:
            skipped += 1
            continue
        if sn.role is not None and sn.role not in config_role_names:
            skipped += 1
            continue
        spec = NodeSpec(id=sn.id, agent=sn.agent, role=sn.role, mode=sn.mode)
        wt = pipeline.workspace.ensure_worktree(spec.id)
        state = NodeState(spec=spec, worktree=wt)
        state.seen = sn.seen
        state.status = sn.last_status if sn.last_status in {
            "idle", "running", "ready", "blocked", "exited"
        } else "idle"
        pipeline.nodes.append(state)
        restored += 1
    return restored, skipped
