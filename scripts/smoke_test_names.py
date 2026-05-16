"""Smoke test: named nodes, AGENTS.md team roster, name-based addressing.

Verifies:
  - Spawning with display_name="Alice" yields id="alice", display="Alice".
  - AGENTS.md gets written into each worktree, listing the team.
  - AGENTS.md updates when a new peer is spawned.
  - `:tell Alice <msg>` resolves by display name and delivers.
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
[agents.capture]
command = ["bash", "-c", "cat > INBOX.txt"]

[pipeline]
name = "naming-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-naming-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)

            alice_id = await app._spawn_node(
                agent="capture", role=None, mode="persistent",
                display_name="Alice",
            )
            assert alice_id == "alice", alice_id
            alice = app.pipeline.node("alice")
            assert alice.spec.display == "Alice"
            print(f"  spawned Alice (id={alice.spec.id})")

            # AGENTS.md should exist in Alice's worktree.
            agents_md = alice.worktree.path / "AGENTS.md"
            assert agents_md.exists(), "AGENTS.md wasn't written"
            text = agents_md.read_text()
            assert "Alice" in text and "alice" in text, text
            assert "No other agents" in text, "should note empty team"
            print(f"  AGENTS.md written, mentions Alice and empty team")

            # Spawn Bob; both worktrees should now list each other.
            bob_id = await app._spawn_node(
                agent="capture", role=None, mode="persistent",
                display_name="Bob",
            )
            assert bob_id == "bob"
            await asyncio.sleep(0.2)

            alice_md = (alice.worktree.path / "AGENTS.md").read_text()
            bob_md = (app.pipeline.node("bob").worktree.path / "AGENTS.md").read_text()
            assert "Bob" in alice_md and "bob" in alice_md, alice_md
            assert "Alice" in bob_md and "alice" in bob_md, bob_md
            # Each should describe ITSELF in the "You are" line, not the other.
            assert "You are **Alice**" in alice_md
            assert "You are **Bob**" in bob_md
            print(f"  AGENTS.md updated for both Alice + Bob, each knows the other")

            # `:tell Alice ...` should resolve by display name.
            app._focus_node("bob")
            await app._dispatch_command("tell Alice hi from bob")
            await asyncio.sleep(0.3)
            import os
            pane_a = app.query_one("#pane-alice")
            os.write(pane_a._proc.fd, b"\x04")  # close cat's stdin
            for _ in range(20):
                if not pane_a.is_alive: break
                await asyncio.sleep(0.1)
            inbox = (alice.worktree.path / "INBOX.txt").read_text()
            assert "hi from bob" in inbox, inbox
            assert "[From bob]" in inbox, inbox
            print(f"  :tell by display name 'Alice' delivered to alice's INBOX")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
