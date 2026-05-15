"""Smoke test: empty start, picker-driven spawn, swap-agent, swap-role.

Headless validates:
  - With no pipeline.toml present, app starts with zero nodes and the
    sidebar's "+ Add agent" row visible.
  - SpawnPickerScreen returns the chosen agent/role/mode, app spawns accordingly.
  - `swap-agent` rebuilds the pane with the new CLI but keeps the worktree.
  - `swap-role` updates the prompt template that will be injected on
    future handoffs.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.one_shot_panel import OneShotPanel
from term.widgets.pty_pane import PtyPane
from term.widgets.sidebar import Sidebar
from term.widgets.spawn_picker import SpawnPickerScreen
from term.workspace import Workspace


# Two cheap CLI stand-ins so we can swap between them.
_CONFIG = """\
[agents.alpha]
command = ["bash", "-c", "echo alpha-running; sleep 30"]

[agents.beta]
command = ["bash", "-c", "echo beta-running; sleep 30"]

[roles.r1]
agent = "alpha"
prompt_template = "role one"

[roles.r2]
agent = "beta"
prompt_template = "role two"

[pipeline]
name = "picker-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-picker-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)

            # 1. Empty start: no node panes mounted.
            assert len(app.pipeline.nodes) == 0, app.pipeline.nodes
            assert app._current_node_id is None
            assert app.query_one(Sidebar) is not None
            print("  empty start ok")

            # 2. Spawn via the API the picker would use.
            nid = await app._spawn_node(agent="alpha", role="r1", mode="persistent")
            assert nid == "r1", nid
            await asyncio.sleep(0.2)
            assert len(app.pipeline.nodes) == 1
            assert app._current_node_id == "r1"
            node = app.pipeline.node("r1")
            assert node.spec.agent == "alpha"
            assert node.spec.role == "r1"
            pane = app.query_one("#pane-r1", PtyPane)
            assert pane is not None
            print(f"  spawned r1 (alpha · r1, persistent)")

            # 3. swap-agent alpha → beta. Pane should be replaced.
            await app._dispatch_command("swap-agent beta")
            await asyncio.sleep(0.2)
            assert node.spec.agent == "beta", node.spec.agent
            assert node.spec.role == "r1", "role preserved"
            new_pane = app.query_one("#pane-r1", PtyPane)
            assert new_pane is not pane, "pane should have been recreated"
            print("  swap-agent → beta ok (pane recreated, role preserved)")

            # 4. swap-role r1 → none.
            await app._dispatch_command("swap-role none")
            await asyncio.sleep(0.1)
            assert node.spec.role is None, node.spec.role
            print("  swap-role → none ok")

            # 5. swap-role none → r2.
            await app._dispatch_command("swap-role r2")
            await asyncio.sleep(0.1)
            assert node.spec.role == "r2"
            print("  swap-role → r2 ok")

            # 6. Spawn a one-shot via the new positional syntax.
            await app._dispatch_command("spawn -o alpha")
            await asyncio.sleep(0.2)
            assert any(
                n.spec.mode == "one-shot" and n.spec.agent == "alpha"
                for n in app.pipeline.nodes
            ), [(n.spec.id, n.spec.agent, n.spec.mode) for n in app.pipeline.nodes]
            print("  spawn -o alpha ok")

            # 7. AddRequested via picker dismiss callback.
            app._on_picker_dismissed({"agent": "beta", "role": "r2", "mode": "persistent"})
            await asyncio.sleep(0.2)
            # Find the new node — should be id "r2-2" (r2 already exists? no, r2 only as role)
            ids = [n.spec.id for n in app.pipeline.nodes]
            print(f"  after picker spawn, node ids: {ids}")
            assert any(i.startswith("r2") for i in ids), ids

            await pilot.press("ctrl+q")

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
