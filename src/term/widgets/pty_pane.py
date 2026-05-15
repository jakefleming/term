"""A Textual widget that runs a child process in a PTY and renders it.

The widget owns a `ptyprocess.PtyProcess` and a `pyte.Screen`. Bytes from the
child are fed into pyte; the resulting virtual screen is rendered line-by-line
into Textual `Strip`s. Keystrokes on the focused widget are translated back
into terminal byte sequences and written to the PTY master fd.
"""

from __future__ import annotations

import asyncio
import os
from typing import Sequence

import pyte
from ptyprocess import PtyProcess
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget


_NAMED_COLORS = {
    "black", "red", "green", "blue", "magenta", "cyan", "white",
}


def _convert_color(c: str | None) -> str | None:
    """Translate pyte's color representation into something Rich accepts."""
    if not c or c == "default":
        return None
    if c == "brown":
        return "yellow"
    if c in _NAMED_COLORS:
        return c
    if c.startswith("bright"):
        return "bright_" + c[len("bright"):]
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return f"#{c}"
    return None


_KEY_BYTES: dict[str, bytes] = {
    "enter": b"\r",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "backspace": b"\x7f",
    "escape": b"\x1b",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "delete": b"\x1b[3~",
    "insert": b"\x1b[2~",
    "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR", "f4": b"\x1bOS",
    "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~",
    "f8": b"\x1b[19~", "f9": b"\x1b[20~", "f10": b"\x1b[21~",
    "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    "space": b" ",
}


def _key_to_bytes(event: events.Key) -> bytes | None:
    key = event.key
    if key in _KEY_BYTES:
        return _KEY_BYTES[key]
    if event.character and event.character.isprintable():
        return event.character.encode("utf-8")
    if key.startswith("ctrl+"):
        ch = key[len("ctrl+"):]
        if len(ch) == 1 and "a" <= ch <= "z":
            return bytes([ord(ch) - 96])
        if ch == "space":
            return b"\x00"
    if key.startswith("alt+"):
        rest = key[len("alt+"):]
        sub = _KEY_BYTES.get(rest)
        if sub:
            return b"\x1b" + sub
        if len(rest) == 1:
            return b"\x1b" + rest.encode("utf-8")
    return None


class PtyPane(Widget, can_focus=True):
    """A widget that hosts a child process inside a PTY."""

    DEFAULT_CSS = """
    PtyPane {
        background: $surface;
        color: $text;
    }
    """

    cursor_visible = reactive(True)

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        name: str | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id)
        self._command = list(command)
        self._cwd = cwd
        self._env_overrides = env or {}
        self._proc: PtyProcess | None = None
        self._screen: pyte.Screen | None = None
        self._stream: pyte.ByteStream | None = None
        self._cols = 80
        self._rows = 24
        self._reader_attached = False

    async def on_mount(self) -> None:
        size = self.size
        cols = size.width if size.width else 80
        rows = size.height if size.height else 24
        self._init_screen(cols, rows)
        self._spawn()
        loop = asyncio.get_running_loop()
        loop.add_reader(self._proc.fd, self._on_readable)
        self._reader_attached = True

    async def on_unmount(self) -> None:
        self._teardown()

    def _init_screen(self, cols: int, rows: int) -> None:
        self._cols = max(int(cols), 1)
        self._rows = max(int(rows), 1)
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.ByteStream(self._screen)

    def _spawn(self) -> None:
        env = dict(os.environ)
        env.update(self._env_overrides)
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        self._proc = PtyProcess.spawn(
            self._command,
            cwd=self._cwd,
            env=env,
            dimensions=(self._rows, self._cols),
        )

    def _teardown(self) -> None:
        if self._proc is None:
            return
        if self._reader_attached:
            try:
                asyncio.get_running_loop().remove_reader(self._proc.fd)
            except Exception:
                pass
            self._reader_attached = False
        try:
            if self._proc.isalive():
                self._proc.terminate(force=True)
        except Exception:
            pass

    def _on_readable(self) -> None:
        if self._proc is None or self._stream is None:
            return
        try:
            data = os.read(self._proc.fd, 65536)
        except OSError:
            data = b""
        if not data:
            # EOF — child exited.
            try:
                asyncio.get_running_loop().remove_reader(self._proc.fd)
                self._reader_attached = False
            except Exception:
                pass
            self.refresh()
            return
        self._stream.feed(data)
        self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        cols = max(event.size.width, 1)
        rows = max(event.size.height, 1)
        if (cols, rows) == (self._cols, self._rows):
            return
        self._cols, self._rows = cols, rows
        if self._screen is not None:
            self._screen.resize(rows, cols)
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if self._screen is None or y >= self._rows:
            return Strip.blank(width)
        line_buf = self._screen.buffer[y]
        cursor = self._screen.cursor
        show_cursor = (
            self.cursor_visible
            and self.has_focus
            and not self._screen.cursor.hidden
            and y == cursor.y
        )
        segments: list[Segment] = []
        for x in range(self._cols):
            ch = line_buf[x]
            char = ch.data or " "
            style = Style(
                color=_convert_color(ch.fg),
                bgcolor=_convert_color(ch.bg),
                bold=ch.bold,
                italic=ch.italics,
                underline=ch.underscore,
                reverse=ch.reverse,
                strike=ch.strikethrough,
            )
            if show_cursor and x == cursor.x:
                style = style + Style(reverse=True)
            segments.append(Segment(char, style))
        return Strip(segments, self._cols)

    async def on_key(self, event: events.Key) -> None:
        if self._proc is None or not self._proc.isalive():
            return
        data = _key_to_bytes(event)
        if data is None:
            return
        event.stop()
        event.prevent_default()
        try:
            os.write(self._proc.fd, data)
        except OSError:
            pass

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()
