"""Smoke test for the diff tray, one-shot nodes, and command-palette dispatch.

Headless: validates that the new features compose, that the command-palette
dispatcher routes verbs correctly, and that a one-shot node runs end-to-end.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.diff_tray import DiffTray
from term.widgets.one_shot_panel import OneShotPanel
from term.widgets.pty_pane import PtyPane
from term.workspace import Workspace


# Use `printer` as a fake agent: echoes the prompt and writes a file.
# This avoids depending on `claude` / `codex` being installed for one-shot test.
_PIPELINE_TOML = """\
[agents.printer]
command = ["bash"]
one_shot = ["bash", "-c", "echo PROMPT={prompt}; echo done > OUT.txt"]

[roles.scribe]
agent = "printer"
prompt_template = "hello world"

[pipeline]
name = "smoke-v2"
nodes = [
  { role = "scribe", mode = "persistent", id = "p1" },
  { role = "scribe", mode = "one-shot",   id = "o1" },
]
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-v2-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_PIPELINE_TOML)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.4)

            # 1. Both nodes mounted, correct widget types.
            assert len(app.pipeline.nodes) == 2
            p1, o1 = app.pipeline.nodes
            persistent_pane = app.query_one("#pane-p1")
            one_shot_panel = app.query_one("#pane-o1")
            assert isinstance(persistent_pane, PtyPane), type(persistent_pane)
            assert isinstance(one_shot_panel, OneShotPanel), type(one_shot_panel)
            print("  pane types ok: PtyPane + OneShotPanel")

            # 2. Diff tray exists and renders.
            tray = app.query_one(DiffTray)
            assert tray is not None
            assert tray._current_label.startswith("p1"), tray._current_label
            print(f"  diff tray initial label: {tray._current_label!r}")

            # 3. Stage a change in p1's worktree, then handoff p1 → o1 (one-shot).
            (p1.worktree.path / "ARTIFACT.txt").write_text("hello\n")
            await app.action_handoff()
            # The one-shot subprocess runs concurrently; don't use pilot.pause
            # (waits for screen-stability and the status tick keeps it busy).
            await asyncio.sleep(0.6)
            # If status hasn't reached ready, give it more time / loop.
            for _ in range(20):
                if o1.status == "ready":
                    break
                await asyncio.sleep(0.1)
            assert (o1.worktree.path / "ARTIFACT.txt").exists(), \
                "handoff didn't deliver artifact to o1"
            assert o1.status == "ready", f"expected ready, got {o1.status!r}"
            # The one-shot subprocess wrote OUT.txt with `done\n`
            out = o1.worktree.path / "OUT.txt"
            assert out.exists(), "one-shot agent didn't run"
            assert out.read_text().strip() == "done"
            print(f"  one-shot ran; status={o1.status}")

            # 4. Spawn a new node via the command-palette dispatcher.
            # New form: `spawn <agent> <role>`. Use printer + scribe.
            await app._dispatch_command("spawn printer scribe")
            assert len(app.pipeline.nodes) == 3
            new_id = app.pipeline.nodes[-1].spec.id
            assert new_id.startswith("scribe"), new_id
            assert app._current_node_id == new_id
            print(f"  spawn via palette → {new_id}")

            # 5. Reroute: focus p1, then `handoff <new_id>`.
            app._focus_node("p1")
            await asyncio.sleep(0.1)
            await app._dispatch_command(f"handoff {new_id}")
            await asyncio.sleep(0.3)
            assert (
                app.pipeline.node(new_id).worktree.path / "ARTIFACT.txt"
            ).exists(), f"rerouted handoff didn't reach {new_id}"
            print(f"  reroute p1 → {new_id} ok")

            # 6. F4 toggles diff tray hidden class.
            assert not tray.has_class("hidden")
            app.action_toggle_diff()
            assert tray.has_class("hidden")
            app.action_toggle_diff()
            assert not tray.has_class("hidden")
            print("  F4 diff toggle ok")

            await pilot.press("ctrl+q")

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
