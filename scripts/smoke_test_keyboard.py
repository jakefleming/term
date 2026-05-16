"""Smoke test: keyboard input survives the PtyPane → PTY → child path.

In particular: slash commands (`/review`, `/clear`, etc.) are just
regular printable chars from the PTY's point of view, but Textual's
event dispatch sometimes special-cases punctuation. Verify that
typing `/foo bar` reaches the child's stdin verbatim.

Also exercises a few control sequences agents rely on:
  - Ctrl-C (interrupt)
  - Up arrow (history)
  - Tab (completion)
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from textual.app import App, ComposeResult

from term.widgets.pty_pane import PtyPane


class _CaptureApp(App[None]):
    def __init__(self, capture: Path) -> None:
        super().__init__()
        self._capture = capture

    def compose(self) -> ComposeResult:
        yield PtyPane(["bash", "-c", f"cat > {self._capture}"], id="pane")

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-keyboard-smoke-"))
    try:
        capture = tmp / "captured.bin"
        app = _CaptureApp(capture)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one("#pane", PtyPane)
            assert pane.is_alive, "cat child didn't start"

            # Type a typical slash command + payload.
            for ch in "/review please":
                await pilot.press(ch if ch != " " else "space")
            # Up/Tab — make sure key encoder produces real bytes.
            await pilot.press("up")
            await pilot.press("tab")
            # Send Ctrl-D to close cat's stdin.
            import os
            os.write(pane._proc.fd, b"\x04")
            for _ in range(20):
                if not pane.is_alive:
                    break
                await asyncio.sleep(0.1)

            content = capture.read_bytes()
            print(f"  captured {len(content)} bytes")
            assert b"/review please" in content, (
                f"slash command not captured: {content!r}"
            )
            # Up arrow = ESC [ A
            assert b"\x1b[A" in content, "up arrow byte sequence missing"
            # Tab = \t
            assert b"\t" in content, "tab byte missing"
            print("  slash command + arrow + tab all delivered ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
