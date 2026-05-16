"""Smoke test: auto-resume falls back to a fresh spawn when the agent's
resume command exits quickly (e.g. `claude --continue` with no prior
conversation in cwd).

The test agent's `resume_args` is a flag that causes it to exit with a
non-zero status almost immediately. Without the watchdog, the user would
see a dead pane after launch. With it, term notices the quick exit and
respawns the base command, leaving a healthy pane.
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


# bash agent that:
#   - writes whatever args it got to LAST_CMD.txt
#   - if "--fail-resume" is present, exits 1 right away
#   - otherwise sleeps forever (healthy pane)
_CONFIG = """\
[agents.flaky]
command = ["bash", "-c", "echo \\"cmd $@\\" > LAST_CMD.txt; for a in \\"$@\\"; do if [ \\"$a\\" = \\"--fail-resume\\" ]; then exit 1; fi; done; sleep 30", "--"]
resume_args = ["--fail-resume"]

[pipeline]
name = "fallback-test"
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
    tmp = Path(tempfile.mkdtemp(prefix="term-resume-fallback-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)

        # 1st launch: spawn the node fresh. Base command, no fail flag,
        # pane stays alive.
        async def first(app, pilot):
            await app._spawn_node(agent="flaky", role=None, mode="persistent")
            node = app.pipeline.node("flaky")
            args_file = node.worktree.path / "LAST_CMD.txt"
            for _ in range(20):
                if args_file.exists(): break
                await asyncio.sleep(0.1)
            text = args_file.read_text().strip()
            assert text == "cmd", f"first launch should use base cmd, got {text!r}"
            assert node.resumed_at is None
            print(f"  1st: spawned flaky, base cmd: {text!r}")

        await _launch_and(tmp, first)

        # 2nd launch: auto-resume tries --fail-resume, agent exits
        # immediately. Watchdog detects and respawns base command.
        async def second(app, pilot):
            node = app.pipeline.node("flaky")
            args_file = node.worktree.path / "LAST_CMD.txt"

            # First wait for the resume attempt to land.
            for _ in range(30):
                if args_file.exists():
                    text = args_file.read_text().strip()
                    if "--fail-resume" in text: break
                await asyncio.sleep(0.1)
            assert "--fail-resume" in args_file.read_text().strip(), \
                "auto-resume should have tried the resume_args first"
            print(f"  2nd: auto-resume attempted: {args_file.read_text().strip()!r}")

            # Now wait for the watchdog to notice the quick exit and
            # respawn fresh. _tick_status fires every 1s.
            ok = False
            for _ in range(80):
                txt = args_file.read_text().strip()
                if txt == "cmd":
                    ok = True
                    break
                await asyncio.sleep(0.1)
            assert ok, (
                f"watchdog should have respawned fresh, "
                f"LAST_CMD.txt is still {args_file.read_text().strip()!r}"
            )
            assert node.resumed_at is None, "fallback should clear resumed_at"
            print(f"  2nd: watchdog respawned fresh: {args_file.read_text().strip()!r}")

        await _launch_and(tmp, second)

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
