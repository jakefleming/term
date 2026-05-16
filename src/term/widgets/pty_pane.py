"""A Textual widget that runs a child process in a PTY and renders it.

The widget owns a `ptyprocess.PtyProcess` and a `pyte.Screen`. Bytes from the
child are fed into pyte; the resulting virtual screen is rendered line-by-line
into Textual `Strip`s. Keystrokes on the focused widget are translated back
into terminal byte sequences and written to the PTY master fd.

If an `image_dir` is provided, the byte stream is pre-filtered to catch
OSC 1337 inline images (which pyte would silently drop), save them to
disk, and substitute a breadcrumb the user can see in the rendered
output.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Sequence

import pyte
from ptyprocess import PtyProcess
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from term.pty_filter import StreamFilter


# Opt-in event tracing: set TERM_DEBUG=1 to log every key / paste / mouse
# event reaching PtyPane to ~/.term-debug.log. Used to diagnose drag-and-drop.
_DEBUG = os.environ.get("TERM_DEBUG") == "1"
_DEBUG_LOG = os.path.expanduser("~/.term-debug.log")


def _dlog(msg: str) -> None:
    if not _DEBUG:
        return
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


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

    class UserLineSubmitted(Message):
        """Posted when the user presses Enter — carries the buffered line.

        Approximate: the buffer accumulates printable keystrokes between
        Enters and accounts for backspace. Arrow-key edits aren't tracked,
        and pasted content is captured via on_paste separately. Good
        enough for soft mood signals.
        """
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class ImageCaptured(Message):
        """Posted when the filter saves an OSC 1337 inline image."""

        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        image_dir: Path | None = None,
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
        self._last_byte_at: float = 0.0
        self._exit_code: int | None = None
        self._user_line_buffer: str = ""
        self._filter: StreamFilter | None = (
            StreamFilter(image_dir=image_dir) if image_dir is not None else None
        )
        self._filter_image_count = 0

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
            try:
                # Reap so exitstatus becomes available.
                self._proc.wait()
                self._exit_code = self._proc.exitstatus
            except Exception:
                self._exit_code = -1
            self.refresh()
            return
        self._last_byte_at = time.monotonic()
        if self._filter is not None:
            data = self._filter.feed(data)
            new_images = self._filter.images[self._filter_image_count:]
            self._filter_image_count = len(self._filter.images)
            for img in new_images:
                self.post_message(self.ImageCaptured(img))
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
        _dlog(f"on_key key={event.key!r} char={event.character!r}")
        if self._proc is None or not self._proc.isalive():
            return
        data = _key_to_bytes(event)
        if data is None:
            _dlog(f"  -> no byte mapping for key={event.key!r}")
            return
        # Track natural-language input for mood signals.
        if event.key == "enter":
            line = self._user_line_buffer
            self._user_line_buffer = ""
            if line.strip():
                self.post_message(self.UserLineSubmitted(line))
        elif event.key == "backspace":
            self._user_line_buffer = self._user_line_buffer[:-1]
        elif event.character and event.character.isprintable():
            if len(self._user_line_buffer) < 4096:
                self._user_line_buffer += event.character
        event.stop()
        event.prevent_default()
        try:
            os.write(self._proc.fd, data)
        except OSError:
            pass

    async def on_paste(self, event: events.Paste) -> None:
        """Forward pastes (incl. macOS drag-and-drop of file paths into the
        outer terminal) as bracketed paste to the child PTY. Without this,
        Textual eats the paste event and the child never sees the text —
        which is why dragging an image into the wrapped Claude Code pane
        does nothing.
        """
        _dlog(f"on_paste text={event.text!r}")
        if self._proc is None or not self._proc.isalive():
            return
        text = event.text
        if not text:
            return
        event.stop()
        event.prevent_default()
        data = b"\x1b[200~" + text.encode("utf-8", errors="replace") + b"\x1b[201~"
        try:
            os.write(self._proc.fd, data)
        except OSError:
            pass

    async def on_mouse_down(self, event: events.MouseDown) -> None:
        _dlog(f"on_mouse_down x={event.x} y={event.y} button={event.button}")

    async def on_mouse_up(self, event: events.MouseUp) -> None:
        _dlog(f"on_mouse_up x={event.x} y={event.y} button={event.button}")

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        await self._forward_wheel(event, button=64)

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        await self._forward_wheel(event, button=65)

    async def _forward_wheel(self, event, *, button: int) -> None:
        """Forward a wheel event to the child as an SGR mouse escape.

        Claude Code / Codex / any TUI that opted into mouse-tracking
        translate these into scrollback navigation. Without this hook
        Textual swallows the wheel event and the child's scrollback is
        unreachable.
        """
        if self._proc is None or not self._proc.isalive():
            return
        event.stop()
        event.prevent_default()
        col = max(1, int(event.x) + 1)
        row = max(1, int(event.y) + 1)
        seq = f"\x1b[<{button};{col};{row};M".encode()
        try:
            os.write(self._proc.fd, seq)
        except OSError:
            pass

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    @property
    def last_byte_at(self) -> float:
        """Monotonic timestamp of the most recent byte from the child, or 0."""
        return self._last_byte_at

    @property
    def exit_code(self) -> int | None:
        """Exit status once the child has exited; None while still alive."""
        return self._exit_code
