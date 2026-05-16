"""Smoke test: mouse-wheel events in a PtyPane reach the child as SGR.

Without forwarding, the inner CLI (claude code, codex, etc.) never
sees the scroll wheel, so its scrollback is unreachable while the
pane has focus. PtyPane.on_mouse_scroll_{up,down} now translate to
`\\x1b[<64;col;row;M` / `\\x1b[<65;col;row;M` and write to the PTY.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from textual import events

from term.widgets.pty_pane import PtyPane


class _CaptureApp(App[None]):
    def __init__(self, capture: Path) -> None:
        super().__init__()
        self._capture = capture

    def compose(self) -> ComposeResult:
        yield PtyPane(["bash", "-c", f"cat > {self._capture}"], id="pane")

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


def _fake_scroll(klass, pane, x: int, y: int):
    """Construct a Textual mouse-scroll event for the given pane."""
    return klass(
        widget=pane,
        x=x, y=y,
        delta_x=0, delta_y=1,
        button=0,
        shift=False, meta=False, ctrl=False,
    )


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-scroll-smoke-"))
    try:
        capture = tmp / "captured.bin"
        app = _CaptureApp(capture)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one("#pane", PtyPane)
            assert pane.is_alive

            # Send a wheel-up then wheel-down at distinct coords.
            await pane.on_mouse_scroll_up(
                _fake_scroll(events.MouseScrollUp, pane, x=5, y=10)
            )
            await pane.on_mouse_scroll_down(
                _fake_scroll(events.MouseScrollDown, pane, x=12, y=3)
            )
            await asyncio.sleep(0.2)

            import os
            os.write(pane._proc.fd, b"\x04")
            for _ in range(20):
                if not pane.is_alive:
                    break
                await asyncio.sleep(0.1)

            content = capture.read_bytes()
            print(f"  captured {len(content)} bytes: {content!r}")
            # Wheel up @ (col=6, row=11), wheel down @ (col=13, row=4).
            assert b"\x1b[<64;6;11;M" in content, "wheel-up SGR missing"
            assert b"\x1b[<65;13;4;M" in content, "wheel-down SGR missing"
            print("  wheel-up + wheel-down forwarded to PTY ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
