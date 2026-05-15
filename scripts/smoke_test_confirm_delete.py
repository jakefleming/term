"""Smoke test: delete-session confirm modal opens, cancel preserves session."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.widgets.confirm_screen import ConfirmScreen
from term.workspace import Workspace


_CONFIG = """\
[pipeline]
name = "confirm-delete-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-confirm-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)

            # Create a second session so there's one we can delete.
            await app._dispatch_command("new-session Extra")
            await asyncio.sleep(0.4)
            assert len(app._sessions.list()) == 2

            # Switch back to default so 'Extra' is not active (can't delete active).
            await app._dispatch_command("switch-session default")
            await asyncio.sleep(0.4)

            extra = next(s for s in app._sessions.list() if s.name == "Extra")
            # Trigger the confirm flow.
            app._confirm_delete_session(extra.id)
            await pilot.pause(0.2)
            assert any(
                isinstance(s, ConfirmScreen) for s in app.screen_stack
            ), "confirm modal didn't open"
            print("  confirm modal opened ✓")

            # Cancel (Esc) — session should still exist.
            await pilot.press("escape")
            await asyncio.sleep(0.2)
            still = [s.name for s in app._sessions.list()]
            assert "Extra" in still, f"cancel should leave session intact, got {still}"
            print("  cancel preserved session ✓")

            # Now confirm for real.
            app._confirm_delete_session(extra.id)
            await pilot.pause(0.2)
            await pilot.press("y")
            await asyncio.sleep(0.4)
            after = [s.name for s in app._sessions.list()]
            assert "Extra" not in after, f"y should have deleted, got {after}"
            print(f"  y deleted session; remaining: {after}")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
