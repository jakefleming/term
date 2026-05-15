"""Smoke test: pipeline persists across app restarts via .term/session.json.

Headless validates:
  - First launch with no session.json initializes from pipeline.toml (empty).
  - Spawning a node persists it to session.json.
  - Second launch in the same workspace restores the node from session.json
    (with seen=True so the ↻ marker would show).
  - :reset-session clears the file.
  - :resume on a node respawns the pane with the agent's resume_args.
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
[agents.recorder]
command = ["bash", "-c", "echo cmd $@ > LAST_CMD.txt; sleep 30", "--"]
resume_args = ["--resumed"]

[pipeline]
name = "session-test"
nodes = []
"""


async def _launch_and(workspace_root: Path, fn) -> None:
    cfg = load_config(workspace_root)
    ws = Workspace.discover(workspace_root)
    app = TermApp(cfg, ws)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        await fn(app, pilot)
        await pilot.press("ctrl+q")


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-session-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)

        # 1st launch: empty pipeline → spawn one node → save.
        async def first(app, pilot):
            assert len(app.pipeline.nodes) == 0
            nid = await app._spawn_node(agent="recorder", role=None, mode="persistent")
            await asyncio.sleep(0.3)
            assert nid == "recorder"
            node = app.pipeline.node("recorder")
            assert node.seen is True, "node should be marked seen after first mount"
            # Initial command should be the base recipe (no resume_args yet).
            args_file = node.worktree.path / "LAST_CMD.txt"
            for _ in range(20):
                if args_file.exists(): break
                await asyncio.sleep(0.1)
            assert args_file.exists()
            text = args_file.read_text().strip()
            assert "--resumed" not in text, f"fresh launch shouldn't use resume_args, got {text!r}"
            print(f"  1st: spawned recorder, base command (no --resumed): {text!r}")

        await _launch_and(tmp, first)

        # session.json should now exist for the default session.
        sess_path = ws.term_dir / "sessions" / "default" / "session.json"
        assert sess_path.exists(), f"session.json wasn't written at {sess_path}"
        data = json.loads(sess_path.read_text())
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["id"] == "recorder"
        assert data["nodes"][0]["seen"] is True
        print("  session.json persisted with seen=True")

        # 2nd launch: pipeline restored from session.json.
        async def second(app, pilot):
            assert len(app.pipeline.nodes) == 1
            node = app.pipeline.node("recorder")
            assert node.seen is True, "restored node should still be seen"
            # The restored launch should have spawned base command again
            # (resume is opt-in via :resume). LAST_CMD.txt should still be
            # the base command from first launch — no --resumed.
            print(f"  2nd: pipeline restored, node.seen={node.seen}")

            # Trigger :resume — pane is torn down, re-mounted with --resumed.
            (node.worktree.path / "LAST_CMD.txt").unlink(missing_ok=True)
            await app._dispatch_command("resume")
            for _ in range(30):
                if (node.worktree.path / "LAST_CMD.txt").exists(): break
                await asyncio.sleep(0.1)
            text = (node.worktree.path / "LAST_CMD.txt").read_text().strip()
            assert "--resumed" in text, f"resume didn't use resume_args, got {text!r}"
            print(f"  2nd: :resume used resume_args: {text!r}")

        await _launch_and(tmp, second)

        # 3rd launch: trigger reset-session, restart, should be empty again.
        async def reset(app, pilot):
            assert len(app.pipeline.nodes) == 1
            await app._dispatch_command("reset-session")
            await asyncio.sleep(0.1)

        await _launch_and(tmp, reset)

        assert not sess_path.exists(), "reset-session should delete session.json"
        print("  reset-session cleared session.json ✓")

        # 4th launch: empty session again.
        async def fourth(app, pilot):
            assert len(app.pipeline.nodes) == 0, app.pipeline.nodes
            print("  4th: pipeline back to empty after reset")

        await _launch_and(tmp, fourth)

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
