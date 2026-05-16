"""Smoke test: spawning a new peer auto-briefs the new node and notifies
existing peers via the messaging primitive.

The user's expectation is "spawn alice, spawn bob, tell either 'talk to
the other' and it works." That requires each agent to know about its
peers and the mailbox convention without you running :brief by hand.
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


# Capture per-pane stdin in INBOX.txt; close stdin to flush.
_CONFIG = """\
[agents.capture]
command = ["bash", "-c", "cat > INBOX.txt"]

[pipeline]
name = "auto-introduce-test"
nodes = []
"""


async def _drain(pane) -> None:
    import os
    os.write(pane._proc.fd, b"\x04")
    for _ in range(20):
        if not pane.is_alive:
            return
        await asyncio.sleep(0.1)


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-introduce-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)

            await app._spawn_node(
                agent="capture", role=None, mode="persistent",
                display_name="Alice",
            )
            # Alice is alone; auto-introduce should be a no-op (no peers).
            await asyncio.sleep(2.0)
            alice = app.pipeline.node("alice")
            alice_pane = app.query_one("#pane-alice")
            assert alice_pane.is_alive

            # Spawn Bob — auto-introduce should brief Bob about Alice and
            # notify Alice about Bob.
            await app._spawn_node(
                agent="capture", role=None, mode="persistent",
                display_name="Bob",
            )
            # Wait for the 1.5s delay + delivery.
            await asyncio.sleep(2.5)

            bob = app.pipeline.node("bob")
            bob_pane = app.query_one("#pane-bob")

            # Close both panes' stdin so cat flushes.
            await _drain(alice_pane)
            await _drain(bob_pane)

            alice_inbox = (alice.worktree.path / "INBOX.txt").read_text()
            bob_inbox = (bob.worktree.path / "INBOX.txt").read_text()

            # Alice (existing peer) should have received the short
            # "new teammate joined" notice mentioning Bob.
            assert "Bob" in alice_inbox or "bob" in alice_inbox, alice_inbox
            assert "New teammate" in alice_inbox, alice_inbox
            print(f"  alice notified of bob's arrival ✓")

            # Bob (new node) should have received the full brief.
            assert "Alice" in bob_inbox or "alice" in bob_inbox, bob_inbox
            assert "peer agents" in bob_inbox or "mail" in bob_inbox, bob_inbox
            assert (
                "bob__to__alice" in bob_inbox or "../../mail/" in bob_inbox
            ), bob_inbox
            print(f"  bob briefed about alice + mailbox convention ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
