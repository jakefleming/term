"""Smoke test: spawn a short-lived child in a PtyPane and check pyte parsed it.

Run with: uv run python scripts/smoke_test.py

We can't visually inspect the Textual render in this sandbox, but we can
confirm: (1) the PTY spawns, (2) bytes flow back, (3) pyte's screen reflects
the expected output. The actual ANSI fidelity vs. `claude` / `codex` has to
be eyeballed in a real terminal — instructions at the bottom of the run.
"""

from __future__ import annotations

import asyncio
import sys

from term.app import SpikeApp
from term.widgets.pty_pane import PtyPane


async def _drive() -> int:
    app = SpikeApp(["printf", "hello\\nworld\\nansi: \\033[31mRED\\033[0m\\n"])
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        pane = app.query_one("#pane", PtyPane)
        screen = pane._screen
        assert screen is not None, "pyte screen never initialized"
        display = [line.rstrip() for line in screen.display]
        joined = "\n".join(display)
        print("---- pyte screen snapshot ----")
        for i, row in enumerate(display[:6]):
            print(f"{i:>2}: {row!r}")
        print("------------------------------")
        assert "hello" in joined, f"expected 'hello' in output, got:\n{joined}"
        assert "world" in joined, f"expected 'world' in output, got:\n{joined}"
        # ANSI red on the RED token — pyte should set fg=red on those cells.
        red_row = screen.buffer[2]
        red_chars = "".join(red_row[i].data for i in range(20))
        red_fgs = [red_row[i].fg for i in range(20)]
        print(f"row 2 text : {red_chars!r}")
        print(f"row 2 fgs  : {red_fgs[:20]}")
        assert "RED" in red_chars, "RED token missing from row 2"
        assert "red" in red_fgs, f"expected pyte to record fg=red, got {set(red_fgs)}"
        await pilot.press("ctrl+q")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
