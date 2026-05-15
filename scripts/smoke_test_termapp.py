"""Mount the real TermApp headlessly and verify it composes without crashing.

We can't eyeball the layout in this sandbox, but a clean mount validates:
  - widgets compose
  - worktrees + PTYs spawn (we use `shell` agent here, not claude/codex)
  - sidebar renders nodes
  - handoff action exists and runs
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.pty_pane import PtyPane
from term.widgets.sidebar import Sidebar
from term.workspace import Workspace


_PIPELINE = """\
[pipeline]
name = "smoke"
nodes = [
  { role = "writer", mode = "persistent", id = "n1" },
  { role = "writer", mode = "persistent", id = "n2" },
]

[roles.writer]
agent = "shell"
prompt_template = "echo hello"
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-app-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_PIPELINE)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)

            sidebar = app.query_one(Sidebar)
            assert sidebar is not None
            panes = app.query(PtyPane)
            assert len(panes) == 2, f"expected 2 panes, got {len(panes)}"
            assert app._current_node_id == "n1", app._current_node_id
            print(f"  mounted; current node = {app._current_node_id}")

            # Create a file in n1's worktree, then trigger handoff.
            n1 = app.pipeline.node("n1")
            (n1.worktree.path / "ARTIFACT.txt").write_text("hello from n1\n")

            await app.action_handoff()
            await pilot.pause(0.3)

            n2_path = app.pipeline.node("n2").worktree.path
            assert (n2_path / "ARTIFACT.txt").exists(), "artifact didn't reach n2"
            assert app._current_node_id == "n2", \
                f"focus should follow handoff, got {app._current_node_id}"
            print(f"  handoff OK; ARTIFACT.txt at {n2_path / 'ARTIFACT.txt'}")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
