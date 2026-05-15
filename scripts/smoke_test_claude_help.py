"""Run `claude --help` inside a PtyPane and inspect pyte's screen state.

Goal: confirm pyte handles the kind of ANSI Claude Code emits without errors,
and produces a sensible visual layout. We can't eyeball the Textual render
from a sandbox, but a clean pyte parse is the prerequisite.
"""

from __future__ import annotations

import asyncio
import shutil
import sys

from term.app import SpikeApp
from term.widgets.pty_pane import PtyPane


async def _drive() -> int:
    if not shutil.which("claude"):
        print("claude CLI not on PATH; skipping")
        return 0
    app = SpikeApp(["claude", "--help"])
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.8)
        pane = app.query_one("#pane", PtyPane)
        screen = pane._screen
        assert screen is not None
        display = [line.rstrip() for line in screen.display]
        non_empty = [line for line in display if line]
        print(f"---- {len(non_empty)} non-empty lines (of {len(display)}) ----")
        for i, line in enumerate(display):
            if line:
                print(f"{i:>3}: {line}")
        styled_cells = 0
        for y in range(screen.lines):
            for x in range(screen.columns):
                ch = screen.buffer[y][x]
                if ch.fg != "default" or ch.bg != "default" or ch.bold:
                    styled_cells += 1
        print(f"---- styled cells: {styled_cells} ----")
        assert non_empty, "claude --help produced no visible output"
        await pilot.press("ctrl+q")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
