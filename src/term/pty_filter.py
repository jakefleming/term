"""Byte-stream filter for capturing terminal escape sequences that
pyte can't render — currently iTerm2's inline-image protocol (OSC 1337).

A Textual-wrapped terminal can't passthrough image bytes to the outer
terminal cleanly (Textual draws cells over the stdout buffer on every
frame). Best we can do without forking Textual is intercept the image
data, save it to a file in the session's image dir, and substitute a
text breadcrumb the user can click / copy to open it externally.

The filter is a small state machine because OSC sequences can span
multiple `read()` chunks. Hand `feed(bytes)` whatever you got from the
PTY; you get back a cleaned byte string to forward to pyte, plus
zero-or-more captured-image paths via the `images` accumulator.
"""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass, field
from pathlib import Path


_ESC = 0x1B
_BEL = 0x07
_OSC = ord("]")
_BACKSLASH = ord("\\")


def _guess_extension(name: str) -> str:
    name = name.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"):
        if name.endswith(ext):
            return ext
    return ".bin"


@dataclass
class StreamFilter:
    """Capture OSC 1337 image sequences out of a PTY byte stream.

    Stateful — keep one instance per pane. Bytes that aren't part of
    an interesting sequence pass through unchanged.
    """

    image_dir: Path
    images: list[Path] = field(default_factory=list)
    # Internal state machine. None = idle; "esc" = saw ESC, awaiting ];
    # "osc" = inside an OSC payload, buffering until BEL or ESC\.
    _state: str | None = field(default=None, repr=False)
    _buf: bytearray = field(default_factory=bytearray, repr=False)
    # Cap any unterminated sequence so a misbehaving child can't grow
    # the buffer without bound.
    _max_pending: int = 16 * 1024 * 1024  # 16 MiB

    def feed(self, data: bytes) -> bytes:
        if not data:
            return data
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if self._state is None:
                if b == _ESC:
                    self._state = "esc"
                    i += 1
                    continue
                out.append(b)
                i += 1
                continue
            if self._state == "esc":
                if b == _OSC:
                    self._state = "osc"
                    self._buf.clear()
                    i += 1
                    continue
                # Not an OSC after all — emit the ESC and re-process this byte.
                out.append(_ESC)
                self._state = None
                continue  # don't advance i; reprocess
            if self._state == "osc":
                # End-of-OSC: BEL or ESC\.
                if b == _BEL:
                    replacement = self._finish_osc()
                    out.extend(replacement)
                    self._state = None
                    self._buf.clear()
                    i += 1
                    continue
                if b == _ESC:
                    # Could be the start of ESC\ terminator. Peek ahead.
                    if i + 1 < n and data[i + 1] == _BACKSLASH:
                        replacement = self._finish_osc()
                        out.extend(replacement)
                        self._state = None
                        self._buf.clear()
                        i += 2
                        continue
                    # Lone ESC inside OSC — abort the sequence, flush
                    # what we had as plain bytes so we don't silently
                    # eat user content.
                    out.append(_ESC)
                    out.append(_OSC)
                    out.extend(self._buf)
                    self._state = "esc"
                    self._buf.clear()
                    i += 1
                    continue
                # Accumulate.
                if len(self._buf) < self._max_pending:
                    self._buf.append(b)
                i += 1
                continue
        return bytes(out)

    def _finish_osc(self) -> bytes:
        """Resolve a completed OSC payload. Returns bytes to splice into
        the cleaned output (usually a text breadcrumb for captured
        images, or empty for OSC we want to drop, or the original
        sequence's text-equivalent for OSC we don't care about)."""
        payload = bytes(self._buf)
        # OSC 1337 (iTerm2): `1337;File=<args>:<base64>`
        if payload.startswith(b"1337;"):
            return self._handle_iterm_osc(payload[len(b"1337;"):])
        # Anything else — drop. pyte would have ignored it anyway.
        # (We do NOT pass it through, because OSC sequences shouldn't
        # be rendered as text.)
        return b""

    def _handle_iterm_osc(self, body: bytes) -> bytes:
        # File=key=val;key=val:<base64>   — capture as image.
        if not body.startswith(b"File="):
            return b""
        rest = body[len(b"File="):]
        if b":" not in rest:
            return b""
        args_blob, b64_blob = rest.split(b":", 1)
        args = {}
        for piece in args_blob.split(b";"):
            if b"=" in piece:
                k, v = piece.split(b"=", 1)
                args[k.decode(errors="replace").strip()] = v.decode(errors="replace").strip()
        # Decode the file name if present.
        name = ""
        if "name" in args:
            try:
                name = base64.b64decode(args["name"]).decode(errors="replace")
            except (binascii.Error, ValueError):
                name = ""
        try:
            raw = base64.b64decode(b64_blob, validate=False)
        except (binascii.Error, ValueError):
            return b"[image data: undecodable]\r\n"
        ext = _guess_extension(name)
        try:
            self.image_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            short = f"{ts}-{len(self.images):03d}{ext}"
            dest = self.image_dir / short
            dest.write_bytes(raw)
            self.images.append(dest)
            label = name or short
            return f"\r\n[image captured · {label} → {dest}]\r\n".encode()
        except OSError as e:
            return f"\r\n[image capture failed: {e}]\r\n".encode()
