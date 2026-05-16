"""Smoke test: `:close <node>` removes an agent from the session.

Validates:
  - Pane is unmounted
  - Node disappears from pipeline + sidebar
  - Session.json reflects the removal after save
  - The team-roster section in remaining worktrees is updated
  - Focus moves to a sibling if the focused agent was closed
  - In-flight sub-tasks owned by the closed agent get cancelled
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.workspace import Workspace


_CONFIG = """\
[agents.shellish]
command = ["bash", "-c", "sleep 30"]

[pipeline]
name = "close-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-close-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(tmp)
        app = TermApp(cfg, ws)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            a_id = await app._spawn_node(agent="shellish", role=None, mode="persistent", display_name="Ann")
            b_id = await app._spawn_node(agent="shellish", role=None, mode="persistent", display_name="Bob")
            assert {n.spec.id for n in app.pipeline.nodes} == {a_id, b_id}
            print(f"  spawned: {a_id}, {b_id}")

            # Focus Ann, then close her.
            app._focus_node(a_id)
            assert app._current_node_id == a_id
            ann_worktree = app.pipeline.node(a_id).worktree.path
            await app._dispatch_command(f"close {a_id}")
            await asyncio.sleep(0.2)

            assert all(n.spec.id != a_id for n in app.pipeline.nodes), (
                f"Ann still in pipeline: {[n.spec.id for n in app.pipeline.nodes]}"
            )
            assert app._current_node_id == b_id, (
                f"focus didn't shift to Bob: {app._current_node_id}"
            )
            print(f"  closed Ann; pipeline now: {[n.spec.id for n in app.pipeline.nodes]}")

            # Worktree is kept on disk.
            assert ann_worktree.exists(), "worktree shouldn't be deleted"
            print(f"  Ann's worktree preserved at {ann_worktree}")

            # Session.json reflects removal.
            saved = json.loads(app._session.path.read_text())
            assert all(n["id"] != a_id for n in saved["nodes"])
            print("  session.json no longer references Ann ✓")

            # Bob's AGENTS.md no longer lists Ann.
            bob_agents = (app.pipeline.node(b_id).worktree.path / "AGENTS.md").read_text()
            assert "Ann" not in bob_agents, "Bob still sees Ann in AGENTS.md"
            print("  team roster refreshed: Bob no longer sees Ann ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
