"""Smoke test: paste events (incl. drag-drop file paths) reach the child PTY.

Drag-and-drop on macOS Terminal / iTerm2 surfaces as a bracketed-paste
sequence to the foreground PTY. Textual converts that to a `Paste` event
which the widget receives BEFORE on_key. Without an explicit on_paste
handler, the event gets eaten and the child never sees the text.

This test mounts a PtyPane running `cat > captured.txt`, then dispatches
a synthetic Paste event into the pane, and verifies the bytes (wrapped in
bracketed-paste escape codes) were written to the child's stdin.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from textual.events import Paste

from term.widgets.pty_pane import PtyPane


class _PasteHostApp(App[None]):
    def __init__(self, capture_path: Path) -> None:
        super().__init__()
        self._capture_path = capture_path

    def compose(self) -> ComposeResult:
        yield PtyPane(
            ["bash", "-c", f"cat > {self._capture_path}"],
            id="pane",
        )

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-paste-smoke-"))
    try:
        capture = tmp / "captured.txt"
        app = _PasteHostApp(capture)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one("#pane", PtyPane)
            assert pane.is_alive, "cat child didn't start"

            payload = "/Users/me/Pictures/screenshot.png"
            await pane.on_paste(Paste(payload))
            await asyncio.sleep(0.2)

            # Close cat's stdin by sending Ctrl-D so the file gets flushed.
            import os
            os.write(pane._proc.fd, b"\x04")
            # Give cat a moment to flush + exit.
            for _ in range(20):
                if not pane.is_alive:
                    break
                await asyncio.sleep(0.1)

            assert capture.exists(), f"cat never wrote {capture}"
            content = capture.read_bytes()
            print(f"  captured {len(content)} bytes")
            assert b"\x1b[200~" in content, "bracketed-paste start missing"
            assert payload.encode() in content, "payload missing"
            assert b"\x1b[201~" in content, "bracketed-paste end missing"
            print("  bracketed paste with payload reached child ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
