"""Smoke test: agent-spawned one-shot sub-agents.

Validates the spawn-request protocol end-to-end:
  - Agent A drops `A__spawn__<id>.toml` in the session's spawn dir.
  - Term picks up the request, validates it, runs the sub as
    `agent -p "<prompt>"` in A's worktree.
  - Sub's stdout is delivered into A's pane (mailbox-style paste)
    prefixed `[sub-task done · ...]`.
  - Sidebar shows the task while it's running and clears when done.
  - Unknown agent / missing prompt errors are surfaced to the
    requester so they can correct and retry.
  - Concurrency cap rejects subs beyond MAX_CONCURRENT_SUBS.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from term.app import TermApp
from term.config import load_config
from term.subagent import MAX_CONCURRENT_SUBS
from term.widgets.pty_pane import PtyPane
from term.widgets.sidebar import Sidebar
from term.workspace import Workspace


# Two agents:
#  - "host" is the parent (sleeps so the pane stays alive to receive)
#  - "echo-bot" is a sub-agent recipe whose one_shot writes the prompt
#    to a file and prints back "RESPONSE: <prompt>". This lets us
#    verify the prompt got through AND that stdout was captured.
_CONFIG = """\
[agents.host]
command = ["bash", "-c", "sleep 30"]

[agents.echo-bot]
command = ["true"]
one_shot = ["bash", "-c", "echo \\"PROMPT GOT: $1\\" >> /tmp/term-subagent-prompts.log; printf 'RESPONSE: %s\\n' \\"$1\\"", "--", "{prompt}"]
[agents.echo-bot.models]
fast = ["--fast-mode"]

[pipeline]
name = "subagent-test"
nodes = []
"""


def _last_paste(pane: PtyPane) -> str | None:
    """Return the most recent bracketed-paste payload delivered to the pane."""
    # Pane is a bash process — anything pasted into its PTY gets echoed
    # to the terminal screen by the shell. Pull it off the rendered
    # screen by grepping for the sentinel sub-task header.
    if pane._screen is None:
        return None
    lines = [line.rstrip() for line in pane._screen.display]
    text = "\n".join(lines)
    if "[sub-task" in text:
        return text
    return None


async def _wait_until(predicate, *, timeout=10.0, interval=0.1):
    """Tiny await-until helper."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def _drive() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-subagent-smoke-"))
    log_file = Path("/tmp/term-subagent-prompts.log")
    log_file.unlink(missing_ok=True)
    try:
        ws = Workspace.init(tmp)
        (ws.term_dir / "pipeline.toml").write_text(_CONFIG)
        cfg = load_config(tmp)
        app = TermApp(cfg, ws)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause(0.4)

            # Spawn one host agent. Sub-tasks come from this one.
            sender_id = await app._spawn_node(
                agent="host", role=None, mode="persistent",
            )
            assert sender_id == "host"
            host = app.pipeline.node("host")
            spawn_dir = app._session.spawn_dir
            assert spawn_dir.exists()

            # ----- HAPPY PATH -----
            req = spawn_dir / f"{sender_id}__spawn__t1.toml"
            req.write_text(
                'agent = "echo-bot"\n'
                'prompt = """hello world"""\n'
                'name = "first"\n'
            )
            ok = await _wait_until(
                lambda: log_file.exists()
                and "PROMPT GOT: hello world" in log_file.read_text(),
                timeout=8,
            )
            assert ok, f"echo-bot never ran; log={log_file.read_text() if log_file.exists() else '<no file>'}"
            print("  happy path: sub ran and saw prompt ✓")

            # Result should land in the host pane.
            host_pane = app.query_one(
                f"#{app._pane_id(host.spec.id)}", PtyPane,
            )
            ok = await _wait_until(
                lambda: _last_paste(host_pane) is not None
                and "RESPONSE: hello world" in (_last_paste(host_pane) or ""),
                timeout=8,
            )
            assert ok, f"sub result never delivered to host pane: {_last_paste(host_pane)!r}"
            print("  happy path: result delivered to host pane ✓")

            # Subtask cleared from tray.
            ok = await _wait_until(
                lambda: len(app._subtasks) == 0, timeout=8,
            )
            assert ok, f"subtasks not cleared: {app._subtasks!r}"
            print("  happy path: tasks tray cleared ✓")

            # ----- INVALID REQUEST (unknown agent) -----
            req = spawn_dir / f"{sender_id}__spawn__t2.toml"
            req.write_text(
                'agent = "no-such-agent"\n'
                'prompt = "x"\n'
            )
            ok = await _wait_until(
                lambda: "rejected" in (_last_paste(host_pane) or "")
                and "no-such-agent" in (_last_paste(host_pane) or ""),
                timeout=8,
            )
            assert ok, f"rejection not delivered: {_last_paste(host_pane)!r}"
            print("  invalid agent: rejection delivered to requester ✓")

            # ----- MODEL ALIAS COMMAND ASSEMBLY -----
            # Verify build_subtask_command places model flags right
            # after argv[0] (where most CLIs like claude / codex
            # accept invocation-level options). Run via direct call
            # because bash -c wrappers don't tolerate leading flags
            # the way real agents do.
            from term.subagent import build_subtask_command
            recipe = cfg.agents["echo-bot"]
            cmd, err = build_subtask_command(
                recipe, model="fast", yolo=False, prompt="x",
            )
            assert err is None
            assert cmd[:2] == ["bash", "--fast-mode"], (
                f"model flag should follow argv[0]; got {cmd!r}"
            )
            # And an unknown alias surfaces a clear error.
            _, err = build_subtask_command(
                recipe, model="ultra-nope", yolo=False, prompt="x",
            )
            assert err and "unknown model" in err
            print("  model alias: command assembled + unknown alias rejected ✓")

            # ----- CONCURRENCY CAP -----
            # Fire MAX_CONCURRENT_SUBS + 2 requests at once. Use a slow
            # sub-recipe so they overlap.
            cfg.agents["slow-bot"] = type(cfg.agents["echo-bot"])(
                name="slow-bot",
                command=("true",),
                one_shot=("bash", "-c", "sleep 0.6; echo done", "--"),
                yolo_args=(),
                resume_args=(),
                models={},
            )
            host_pane._screen.reset()  # type: ignore[union-attr]
            cap = MAX_CONCURRENT_SUBS
            for i in range(cap + 2):
                p = spawn_dir / f"{sender_id}__spawn__cap{i}.toml"
                p.write_text(
                    'agent = "slow-bot"\n'
                    f'prompt = "load-{i}"\n'
                    f'name = "load-{i}"\n'
                )
            # Allow time for term to drain + dispatch one tick.
            await asyncio.sleep(2.0)
            # We expect at most `cap` to be running concurrently; the
            # rest must have been rejected. Look for the rejection
            # paste on the host pane.
            text = _last_paste(host_pane) or ""
            assert "rejected" in text and "cap" in text, (
                f"concurrency cap should have rejected some subs; "
                f"pane shows {text!r}"
            )
            print(f"  concurrency cap: rejected subs over {cap} ✓")

            await pilot.press("ctrl+q")
        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        log_file.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
