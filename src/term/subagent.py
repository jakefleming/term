"""One-shot sub-tasks spawned by running agents.

A SubTask is a short-lived `agent -p "<prompt>"` invocation requested by
one of the active agents (via a file dropped in the session's spawn dir).
Term runs it in the requester's worktree, captures stdout, and delivers
the result back to the requester's mailbox so it lands at their prompt
the same way peer messages do.

This is the cheap-but-thorough pattern: parent stays focused, sub does
the heavy lifting with its own (possibly different) model, only the
summarized result flows back into the parent's context.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from term.config import AgentRecipe


# Hard caps so a runaway agent can't fork a million subs.
MAX_CONCURRENT_SUBS = 5
DEFAULT_TIMEOUT_S = 300.0  # 5 min — long enough for thoughtful subs, short
                            # enough that hung children release their slot.
MAX_OUTPUT_BYTES = 256 * 1024  # cap delivered payload to 256 KiB.


@dataclass
class SubTask:
    """One in-flight (or recently-completed) sub-task."""

    id: str
    sender_id: str          # the agent that requested this sub
    agent_name: str         # recipe.name (claude-code, codex, …)
    model: str | None       # alias the requester asked for ("opus", …)
    display: str            # short label for the tasks tray
    prompt: str
    cwd: Path
    started_at: float       # monotonic
    status: str = "queued"  # queued | running | done | failed | cancelled
    rc: int | None = None
    output: str = ""        # captured stdout (+ merged stderr); grows
                            # while the sub is running so the inspector
                            # can stream it live.
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_live(self) -> bool:
        return self.status in {"queued", "running"}


def build_subtask_command(
    recipe: AgentRecipe,
    *,
    model: str | None,
    yolo: bool,
    prompt: str,
) -> tuple[list[str], str | None]:
    """Assemble the argv for a sub-task.

    Returns `(cmd, error)`. If `error` is non-None the caller should fail
    the task with that message rather than spawn — e.g. when the agent
    has no `one_shot` recipe or the requested model alias is unknown.
    """
    if not recipe.one_shot:
        return [], (
            f"agent {recipe.name!r} has no one_shot recipe; "
            f"can't run sub-tasks against it"
        )
    parts: list[str] = list(recipe.one_shot)
    # Inject model flags right after argv[0] so they're seen as
    # invocation-level options, not as part of the prompt that gets
    # substituted in further down.
    if model is not None:
        flags = recipe.models.get(model)
        if flags is None:
            return [], (
                f"unknown model {model!r} for agent {recipe.name!r}; "
                f"available: {sorted(recipe.models) or '(none configured)'}"
            )
        parts = [parts[0]] + list(flags) + parts[1:]
    if yolo and recipe.yolo_args:
        # Append yolo_args at the end (same as _yolo_extend) — most CLIs
        # accept trailing flags fine.
        parts = parts + list(recipe.yolo_args)
    cmd = [a.replace("{prompt}", prompt) for a in parts]
    return cmd, None


def new_id() -> str:
    return uuid.uuid4().hex[:8]


async def run_subtask(sub: SubTask, cmd: list[str]) -> None:
    """Spawn the sub, drain its stdout into `sub.output`, set status.

    Coroutine — caller wraps in asyncio.create_task and stores the task
    on `sub.task` so it can be cancelled if the parent goes away.
    """
    sub.status = "running"
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(sub.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as e:
        sub.status = "failed"
        sub.error = str(e)
        sub.rc = 127
        return

    total = 0
    truncated = False
    assert proc.stdout is not None
    try:
        async def _drain() -> None:
            nonlocal total, truncated
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    return
                total += len(chunk)
                # Stream into sub.output as bytes arrive so the
                # inspector UI can watch live. Cap the buffer; once we
                # hit the cap, keep counting (so we can report it) but
                # stop appending.
                if total <= MAX_OUTPUT_BYTES:
                    sub.output += chunk.decode(errors="replace")
                elif not truncated:
                    head_room = MAX_OUTPUT_BYTES - (total - len(chunk))
                    if head_room > 0:
                        sub.output += chunk[:head_room].decode(errors="replace")
                    sub.output += f"\n…[output truncated at {MAX_OUTPUT_BYTES} bytes]"
                    truncated = True

        await asyncio.wait_for(_drain(), timeout=DEFAULT_TIMEOUT_S)
        rc = await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        sub.status = "failed"
        sub.error = f"timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        sub.rc = -1
        return
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        sub.status = "cancelled"
        sub.rc = -1
        raise

    sub.rc = rc
    sub.status = "done" if rc == 0 else "failed"


def render_delivery(sub: SubTask) -> str:
    """Format the sub's result for delivery to the sender's mailbox."""
    header = (
        f"[sub-task done · {sub.agent_name}"
        + (f" · {sub.model}" if sub.model else "")
        + (f" · {sub.display}" if sub.display and sub.display != sub.id else "")
        + f" · rc={sub.rc}]"
    )
    body = sub.output.strip()
    if sub.status != "done":
        err = sub.error or f"exited with rc={sub.rc}"
        return f"{header}\n{err}\n\n{body}".rstrip() + "\n"
    return f"{header}\n{body}\n"
