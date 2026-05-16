"""Smoke test: sub-task inspector + live streaming.

Validates two things:
  - SubTask.output grows incrementally while the sub is running
    (so the inspector can tail it live, not just see it at the end).
  - Clicking a task row in the sidebar opens the inspector modal
    and the modal renders the right header / prompt / output.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.sidebar import Sidebar
from term.widgets.subtask_inspector import SubTaskInspectorScreen
from term.workspace import Workspace


# Sub emits 5 lines, one per 0.2s, so we have time to observe partial
# output before the sub completes.
_CONFIG = """\
[agents.host]
command = ["bash", "-c", "sleep 30"]

[agents.dripper]
command = ["true"]
one_shot = ["bash", "-c", "for i in 1 2 3 4 5; do echo line-$i; sleep 0.2; done", "--", "{prompt}"]

[pipeline]
name = "inspector-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-inspector-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(tmp)
        app = TermApp(cfg, ws)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause(0.4)
            await app._spawn_node(agent="host", role=None, mode="persistent")
            host = app.pipeline.node("host")

            spawn_dir = app._session.spawn_dir
            (spawn_dir / "host__spawn__drip.toml").write_text(
                'agent = "dripper"\n'
                'prompt = "stream me"\n'
                'name = "drip"\n'
            )

            # Wait for the sub to register.
            for _ in range(40):
                if app._subtasks:
                    break
                await asyncio.sleep(0.1)
            assert app._subtasks, "spawn never reached _subtasks"
            sub = app._subtasks[0]
            print(f"  sub registered: {sub.id} {sub.display}")

            # Wait for partial output (at least one line, less than five).
            for _ in range(40):
                lines = sub.output.count("line-")
                if 1 <= lines < 5:
                    break
                await asyncio.sleep(0.1)
            partial = sub.output
            assert "line-1" in partial, f"no streaming yet: {partial!r}"
            assert partial.count("line-") < 5, (
                f"output already complete before we observed streaming: {partial!r}"
            )
            print(f"  streaming verified: partial output = {partial.strip()!r}")

            # Push the inspector while the sub is still running so we
            # exercise the live-tail code path.
            await app.push_screen(SubTaskInspectorScreen(sub))
            await pilot.pause(0.2)
            inspector = app.screen
            assert isinstance(inspector, SubTaskInspectorScreen), (
                f"inspector didn't become the top screen: {type(inspector)}"
            )
            from textual.widgets import Static
            header = inspector.query_one("#subtask-header", Static)
            header_text = str(header.render())
            assert "host" in header_text and "dripper" in header_text, (
                f"header missing sender/agent: {header_text!r}"
            )
            print(f"  inspector mounted: header={header_text!r}")

            # Wait for the sub to finish; inspector should still be open
            # and reflect the final status.
            for _ in range(60):
                if sub.status == "done":
                    break
                await asyncio.sleep(0.1)
            assert sub.status == "done", f"sub never finished: {sub.status}"
            # Let one tick of the inspector run so it re-reads.
            await asyncio.sleep(0.5)
            meta_text = str(inspector.query_one("#subtask-meta", Static).render())
            assert "done" in meta_text, f"meta didn't refresh: {meta_text!r}"
            assert "line-5" in sub.output, "final output missing last line"
            print(f"  finalized: meta={meta_text!r}")

            # Close the inspector.
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SubTaskInspectorScreen), (
                "esc didn't close the inspector"
            )
            print("  esc closes inspector ✓")

            # Tasks tray should drop the completed sub.
            for _ in range(40):
                if not app._subtasks:
                    break
                await asyncio.sleep(0.1)
            assert not app._subtasks, f"tray not cleared: {app._subtasks!r}"
            print("  tasks tray cleared after completion ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
