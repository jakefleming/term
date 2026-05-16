"""Smoke test: OSC 1337 inline images get captured to disk.

pyte can't render images and the Textual cell renderer can't passthrough
raw image bytes, so PtyPane wires a StreamFilter that intercepts iTerm2
OSC 1337 sequences, saves the payload to the session's image_dir, and
emits an ImageCaptured message. The breadcrumb is also rendered into
the pane's text so the user can see what happened.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import sys
import tempfile
from pathlib import Path

from textual.app import App, ComposeResult

from term.widgets.pty_pane import PtyPane


# 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000100020e7c500000000049454e44ae426082"
)


def _build_osc_1337(payload: bytes, name: str = "pixel.png") -> bytes:
    name_b64 = base64.b64encode(name.encode()).decode()
    body_b64 = base64.b64encode(payload).decode()
    return (
        b"\x1b]1337;File=name=" + name_b64.encode()
        + b";inline=1:" + body_b64.encode()
        + b"\x07"
    )


class _ImageEmitterApp(App[None]):
    def __init__(self, image_dir: Path) -> None:
        super().__init__()
        self._image_dir = image_dir
        self.captured: list[Path] = []

    def compose(self) -> ComposeResult:
        # Spawn a bash that emits the OSC 1337 sequence then sits idle.
        # Use a here-file to keep the binary payload exact.
        yield PtyPane(
            ["bash", "-c", "cat $0; sleep 5", str(self._fixture_path)],
            image_dir=self._image_dir,
            id="pane",
        )

    @property
    def _fixture_path(self) -> Path:
        return self._image_dir / "osc-fixture.bin"

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()

    def on_pty_pane_image_captured(
        self, message: PtyPane.ImageCaptured,
    ) -> None:
        self.captured.append(message.path)


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-image-smoke-"))
    try:
        image_dir = tmp / "images"
        image_dir.mkdir()

        # Pre-write the OSC fixture; bash will cat it into the PTY.
        osc = _build_osc_1337(_PNG)
        (image_dir / "osc-fixture.bin").write_bytes(osc)

        app = _ImageEmitterApp(image_dir)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            pane = app.query_one("#pane", PtyPane)
            assert pane.is_alive

            # Wait for the filter to land an image.
            for _ in range(40):
                if app.captured:
                    break
                await asyncio.sleep(0.1)
            assert app.captured, "OSC 1337 never produced an image"
            saved = app.captured[0]
            assert saved.exists(), f"image path not on disk: {saved}"
            assert saved.read_bytes() == _PNG, "saved bytes don't match input"
            print(f"  image captured → {saved.name}")

            # The breadcrumb should appear in the rendered screen.
            await asyncio.sleep(0.2)
            display = "\n".join(line.rstrip() for line in pane._screen.display)
            assert "image captured" in display, (
                f"breadcrumb missing from pane render: {display!r}"
            )
            print("  breadcrumb rendered in pane ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
