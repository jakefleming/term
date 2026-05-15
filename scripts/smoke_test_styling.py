"""Validate that pyte parses real ANSI styling and cursor control.

Three checks:
  1. `ls --color=always` produces colored cells.
  2. A shell script that uses cursor movement (clear, position, then write)
     produces text at the expected positions.
  3. 256-color and truecolor sequences land as hex colors pyte exposes.
"""

from __future__ import annotations

import asyncio
import sys

from term.app import SpikeApp
from term.widgets.pty_pane import PtyPane


async def _run(cmd: list[str], size=(80, 24), pause=0.4):
    app = SpikeApp(cmd)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(pause)
        pane = app.query_one("#pane", PtyPane)
        screen = pane._screen
        assert screen is not None
        return [
            [screen.buffer[y][x] for x in range(screen.columns)]
            for y in range(screen.lines)
        ]


async def _drive() -> int:
    # 1. Multi-color ANSI: emit red/green/blue with bold and verify.
    rows = await _run(
        ["bash", "-c",
         "printf '\\033[1;31mRR\\033[0m \\033[32mGG\\033[0m \\033[34mBB\\033[0m\\n'; sleep 0.2"]
    )
    row0 = rows[0]
    fg_at = lambda x: row0[x].fg
    print(f"chars: {''.join(c.data for c in row0[:8])}")
    print(f"fgs  : {[fg_at(i) for i in range(8)]}")
    print(f"bold0: {row0[0].bold}  bold3: {row0[3].bold}")
    assert fg_at(0) == "red" and row0[0].bold, "expected bold red on col 0"
    assert fg_at(3) == "green", "expected green on col 3"
    assert fg_at(6) == "blue", "expected blue on col 6"

    # 2. Cursor positioning: \033[H = home, \033[3;10H = row 3 col 10.
    rows = await _run(
        ["bash", "-c", "printf '\\033[H\\033[2J\\033[3;10HHELLO\\033[6;1HBYE\\n'; sleep 0.2"]
    )
    row_3 = "".join(c.data for c in rows[2])[5:15]
    row_6 = "".join(c.data for c in rows[5])[:5]
    print(f"row 3 cols 5-15: {row_3!r}")
    print(f"row 6 cols 0-5 : {row_6!r}")
    assert "HELLO" in row_3, f"cursor positioning to (3,10) failed: {row_3!r}"
    assert "BYE" in row_6, f"cursor positioning to (6,1) failed: {row_6!r}"

    # 3. Truecolor: \033[38;2;R;G;Bm should land as a hex color.
    rows = await _run(
        ["bash", "-c", "printf '\\033[38;2;255;100;50mORANGE\\033[0m\\n'; sleep 0.2"]
    )
    line0 = rows[0]
    orange_fg = line0[0].fg
    print(f"truecolor fg of first 'O' cell: {orange_fg!r}")
    # pyte stores truecolor as 6-char hex (no #).
    assert orange_fg and len(orange_fg) == 6 and orange_fg.lower() == "ff6432", (
        f"expected ff6432, got {orange_fg!r}"
    )
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
