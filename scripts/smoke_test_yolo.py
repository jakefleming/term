"""Smoke test: --yolo / --dangerously-skip-permissions wiring.

Verifies that when TermApp is constructed with yolo=True, an agent that
declares `yolo_args = [...]` gets those args spliced into its spawned
command — for both persistent panes and one-shot subprocesses.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.workspace import Workspace


_CONFIG = """\
# An agent that records its argv to a file so we can read it back.
[agents.recorder]
command = ["bash", "-c", "echo $@ > ARGS.txt; sleep 30", "--"]
one_shot = ["bash", "-c", "echo $@ > ONE_SHOT_ARGS.txt", "--"]
yolo_args = ["--dangerously-skip-permissions", "--also-this"]

[roles.scribe]
agent = "recorder"
prompt_template = "ignored"

[pipeline]
name = "yolo-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-yolo-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)

        # --- yolo OFF: spawning recorder should NOT include the yolo args.
        app = TermApp(cfg, ws, yolo=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            nid = await app._spawn_node(agent="recorder", role=None, mode="persistent")
            await asyncio.sleep(0.5)
            node = app.pipeline.node(nid)
            args_file = node.worktree.path / "ARGS.txt"
            assert args_file.exists(), "recorder didn't write ARGS.txt"
            args_off = args_file.read_text().strip()
            print(f"  yolo=off ARGS.txt: {args_off!r}")
            assert "--dangerously-skip-permissions" not in args_off
            await pilot.press("ctrl+q")

        # --- yolo ON: same agent should get the args.
        app = TermApp(cfg, ws, yolo=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            nid = await app._spawn_node(agent="recorder", role=None, mode="persistent")
            await asyncio.sleep(0.5)
            node = app.pipeline.node(nid)
            args_file = node.worktree.path / "ARGS.txt"
            assert args_file.exists(), "recorder didn't write ARGS.txt"
            args_on = args_file.read_text().strip()
            print(f"  yolo=on  ARGS.txt: {args_on!r}")
            assert "--dangerously-skip-permissions" in args_on
            assert "--also-this" in args_on

            # one-shot path: spawn -o, hand off so it runs.
            persistent_nid = nid
            one_shot_nid = await app._spawn_node(agent="recorder", role="scribe", mode="one-shot")
            (node.worktree.path / "TRIGGER.txt").write_text("go\n")
            # focus persistent again, handoff to one-shot
            app._focus_node(persistent_nid)
            await asyncio.sleep(0.1)
            await app._dispatch_command(f"handoff {one_shot_nid}")
            await asyncio.sleep(0.6)
            one_shot_args = (app.pipeline.node(one_shot_nid).worktree.path
                             / "ONE_SHOT_ARGS.txt")
            assert one_shot_args.exists(), "one-shot didn't run"
            args_text = one_shot_args.read_text().strip()
            print(f"  yolo=on  one-shot ARGS: {args_text!r}")
            assert "--dangerously-skip-permissions" in args_text
            await pilot.press("ctrl+q")

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
