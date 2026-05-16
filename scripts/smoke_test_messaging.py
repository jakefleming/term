"""Smoke test: inter-agent messaging via `:tell` and file mailbox.

Verifies:
  - `:tell <node> <message>` injects bracketed-paste into the target pane's PTY.
  - Dropping `to-<target>.txt` in the session mail dir delivers + cleans up.
  - Messages reach the child as text (we use `cat` to capture).
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
name = "messaging-test"
nodes = []
"""


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-msg-smoke-"))
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(ws.root)
        app = TermApp(cfg, ws)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)

            nid_a = await app._spawn_node(agent="capture", role=None, mode="persistent")
            nid_b = await app._spawn_node(agent="capture", role=None, mode="persistent")
            await asyncio.sleep(0.3)

            # 1. `:tell <target> <message>` from a (currently focused) → b.
            app._focus_node(nid_a)
            await app._dispatch_command(f"tell {nid_b} hello from a")
            await asyncio.sleep(0.4)

            # Close b's stdin so cat flushes to INBOX.txt.
            import os
            pane_b = app.query_one(f"#pane-{nid_b}")
            os.write(pane_b._proc.fd, b"\x04")  # ^D
            for _ in range(20):
                if not pane_b.is_alive:
                    break
                await asyncio.sleep(0.1)

            inbox_b = app.pipeline.node(nid_b).worktree.path / "INBOX.txt"
            assert inbox_b.exists(), "tell didn't reach b's cat"
            content = inbox_b.read_text()
            assert "[From " in content and "hello from a" in content, content
            print(f"  :tell ok: b's INBOX has {content!r}")

            # 2. Mailbox file drop → delivered to a.
            assert app._session is not None
            mail_path = app._session.mail_dir / f"to-{nid_a}.txt"
            mail_path.write_text("greetings via mailbox\n")
            # Wait for the mail tick.
            for _ in range(30):
                await asyncio.sleep(0.2)
                if not mail_path.exists():
                    break
            assert not mail_path.exists(), "mail file wasn't consumed"
            pane_a = app.query_one(f"#pane-{nid_a}")
            os.write(pane_a._proc.fd, b"\x04")
            for _ in range(20):
                if not pane_a.is_alive:
                    break
                await asyncio.sleep(0.1)
            inbox_a = app.pipeline.node(nid_a).worktree.path / "INBOX.txt"
            assert inbox_a.exists(), "mailbox-delivered message didn't reach a"
            content_a = inbox_a.read_text()
            assert "greetings via mailbox" in content_a, content_a
            print(f"  mailbox ok: a's INBOX has {content_a!r}")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
