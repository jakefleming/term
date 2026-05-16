"""Smoke test: the sidebar avatar follows the currently-focused agent.

Spawn two agents with names that hash to distinct avatar templates,
verify the rendered art differs after focusing each, and verify it
falls back to the session avatar when nothing is focused.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.avatar import template_for
from term.config import load_config
from term.widgets.sidebar import Sidebar
from term.widgets.session_card import SessionCard
from term.workspace import Workspace


_CONFIG = """\
[agents.shellish]
command = ["bash", "-c", "sleep 30"]

[pipeline]
name = "avatar-focus-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-avatar-focus-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(tmp)
        app = TermApp(cfg, ws)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            card = app.query_one(SessionCard)
            sidebar = app.query_one(Sidebar)

            # Pick names that hash to different templates so the test
            # is robust against template_for's keyword-or-hash logic.
            a, b = "Robobot", "Ghosty"
            assert template_for(a) != template_for(b), (
                f"need distinct templates for the test; both map to "
                f"{template_for(a)}"
            )

            a_id = await app._spawn_node(
                agent="shellish", role=None, mode="persistent",
                display_name=a,
            )
            await asyncio.sleep(0.1)
            art_a = str(card._art.render())
            assert art_a.strip(), "avatar art is empty after first spawn"

            b_id = await app._spawn_node(
                agent="shellish", role=None, mode="persistent",
                display_name=b,
            )
            await asyncio.sleep(0.1)
            art_b = str(card._art.render())
            assert art_b != art_a, (
                f"avatar didn't change when focus moved to {b}; "
                f"a={art_a!r}\nb={art_b!r}"
            )
            print(f"  avatar changed Robobot → Ghosty ✓")

            # Manual focus back to A.
            app._focus_node(a_id)
            await asyncio.sleep(0.1)
            art_a2 = str(card._art.render())
            assert art_a2 == art_a, "avatar should match when re-focusing A"
            print(f"  avatar restored when focusing back ✓")

            # Close both → fallback to session avatar (no agent focused).
            await app._dispatch_command(f"close {a_id}")
            await app._dispatch_command(f"close {b_id}")
            await asyncio.sleep(0.1)
            agent_label = str(card._agent_label.render())
            assert "no agent focused" in agent_label.lower(), (
                f"empty state should show 'no agent focused', got {agent_label!r}"
            )
            print(f"  empty-state label ✓ ({agent_label!r})")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
