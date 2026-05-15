"""Smoke test: persistent-node status flips with PTY activity / exit."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.pty_pane import PtyPane
from term.widgets.help_screen import HelpScreen
from term.workspace import Workspace


# `chatter` keeps printing then exits. We watch its status transition through
# running -> exited as the app's status tick observes activity then EOF.
_PIPELINE = """\
[agents.chatter]
command = ["bash", "-c", "for i in $(seq 1 12); do echo line $i; sleep 0.2; done"]

[roles.gabber]
agent = "chatter"
prompt_template = "ignored"

[pipeline]
name = "polish"
nodes = [
  { role = "gabber", mode = "persistent", id = "a" },
]
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-polish-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_PIPELINE)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            node = app.pipeline.node("a")
            pane = app.query_one("#pane-a", PtyPane)
            assert pane is not None
            print(f"  initial status: {node.status}")

            # Should see "running" while chatter is producing output.
            saw_running = False
            for _ in range(15):
                await pilot.pause(0.15)
                if node.status == "running":
                    saw_running = True
                    break
            assert saw_running, f"never saw 'running' status (last: {node.status})"
            print(f"  observed running: {node.status}")

            # After chatter exits + ACTIVITY_WINDOW elapses, status -> exited.
            for _ in range(40):
                await pilot.pause(0.15)
                if pane.exit_code is not None and node.status == "exited":
                    break
            assert pane.exit_code is not None, "pane never reported exit"
            assert node.status == "exited", f"status didn't reach 'exited': {node.status}"
            print(f"  observed exit: code={pane.exit_code} status={node.status}")

            # F12 opens the help screen.
            await pilot.press("f12")
            await pilot.pause(0.1)
            assert any(
                isinstance(s, HelpScreen) for s in app.screen_stack
            ), "help screen didn't open"
            print("  F12 help screen opened")
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("ctrl+q")

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
