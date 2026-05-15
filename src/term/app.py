"""Textual apps for term.

Two apps live here:

- `TermApp` — the real thing. Loads a pipeline, ensures a worktree per node,
  spawns one persistent PtyPane or one-shot panel per node, shows a sidebar
  to switch focus, a diff tray on the right, and a `:` command palette.
- `SpikeApp` — single-pane smoke-test app for poking at the PTY widget in
  isolation. Reachable via `term spike -- <cmd>`.

Key bindings (app-level priority — PTY panes do not see these):
  Ctrl-Q   quit
  F1       command palette
  F2       handoff focused → next
  F3       open pending files in $EDITOR
  F4       toggle diff tray
  F5       focus sidebar (toggle)
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Static

from term.config import Config, NodeSpec
from term.handoff import handoff as do_handoff
from term.pipeline import NodeState, PipelineRun
from term.session import (
    Session,
    SessionInfo,
    SessionManager,
    restore_pipeline,
    seed_pipeline_from_config,
)
from term.widgets.session_picker import SessionPickerScreen, _SessionRow
from term.widgets.command_palette import CommandPaletteScreen
from term.widgets.diff_tray import DiffTray
from term.widgets.help_screen import HelpScreen
from term.widgets.one_shot_panel import OneShotPanel
from term.widgets.pty_pane import PtyPane
from term.widgets.sidebar import Sidebar
from term.widgets.spawn_picker import SpawnPickerScreen
from term.workspace import Workspace, git


class SpikeApp(App[None]):
    """One pane, one child process. The smallest useful thing."""

    CSS = """
    Screen { background: $surface; }
    PtyPane { width: 100%; height: 100%; }
    """

    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, command: Sequence[str], cwd: str | None = None) -> None:
        super().__init__()
        self._command = list(command)
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        yield PtyPane(self._command, cwd=self._cwd, id="pane")

    def on_mount(self) -> None:
        self.query_one("#pane", PtyPane).focus()


class TermApp(App[None]):
    """Pipeline + per-node panes + handoff + diff tray + palette."""

    CSS = """
    Screen { background: $surface; layout: vertical; }
    #body { height: 1fr; layout: horizontal; }
    PtyPane, OneShotPanel { width: 100%; height: 100%; }
    ContentSwitcher#panes { width: 1fr; height: 100%; background: $surface; }
    DiffTray.hidden { display: none; }
    #empty-state {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    """

    # Time after which a persistent pane with no recent bytes is "idle."
    _ACTIVITY_WINDOW = 2.5

    BINDINGS = [
        Binding("ctrl+q",  "quit",                  "Quit",     priority=True),
        Binding("f1",      "open_palette",          "Palette",  priority=True),
        Binding("f2",      "handoff",               "Handoff →", priority=True),
        Binding("f3",      "edit_artifact",         "Edit",     priority=True),
        Binding("f4",      "toggle_diff",           "Diff",     priority=True),
        Binding("f5",      "toggle_sidebar_focus",  "Pipeline", priority=True),
        Binding("f6",      "add_agent",             "+ Add",    priority=True),
        Binding("f8",      "toggle_mouse",          "Mouse",    priority=True),
        Binding("f9",      "open_session_picker",   "Sessions", priority=True),
        Binding("f12",     "open_help",             "Help",     priority=True),
    ]

    def __init__(
        self,
        config: Config,
        workspace: Workspace,
        *,
        yolo: bool = False,
        mouse: bool | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.workspace = workspace
        self.pipeline = PipelineRun(config, workspace)
        self._current_node_id: str | None = None
        self._yolo = yolo
        # Default mouse on; F8 toggles it off mid-session for native text
        # selection. Drag-drop works in either mode (handled at App level).
        self._mouse = True if mouse is None else mouse
        self._sessions = SessionManager(workspace)
        self._session: Session | None = None  # set in on_mount

    async def on_paste(self, event) -> None:
        """App-level paste handler.

        Drag-and-drop in Ghostty (and likely others) delivers the Paste
        event to the App directly, not to the focused widget — so the
        per-pane `PtyPane.on_paste` never fires. We forward the paste
        content to the current pane's PTY here as a fallback, and stop
        the event so the widget-level handler doesn't double-write for
        normal cmd+V (which bubbles up).
        """
        if os.environ.get("TERM_DEBUG") == "1":
            try:
                with open(os.path.expanduser("~/.term-debug.log"), "a") as f:
                    focused = (
                        type(self.focused).__name__ if self.focused else None
                    )
                    f.write(
                        f"{time.time():.3f} APP on_paste focused={focused} "
                        f"current={self._current_node_id} text={event.text!r}\n"
                    )
            except Exception:
                pass
        text = event.text
        if not text or self._current_node_id is None:
            return
        try:
            pane = self.query_one(
                f"#{self._pane_id(self._current_node_id)}", PtyPane
            )
        except Exception:
            return
        if not pane.is_alive or pane._proc is None:
            return
        event.stop()
        data = b"\x1b[200~" + text.encode("utf-8", errors="replace") + b"\x1b[201~"
        try:
            os.write(pane._proc.fd, data)
        except OSError:
            pass

    def _yolo_extend(self, base: tuple[str, ...] | list[str], recipe) -> list[str]:
        """Append yolo_args when --yolo is on.

        Append (rather than splice after argv[0]) so wrapper-style commands
        (`bash -c '...' --`) don't pick up the flags as their own. For
        flat invocations like `claude -p {prompt}`, argparse-style CLIs
        handle trailing flags fine.
        """
        cmd = list(base)
        if self._yolo and recipe.yolo_args:
            cmd.extend(recipe.yolo_args)
        return cmd

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Sidebar(self.pipeline, id="sidebar")
            with ContentSwitcher(id="panes", initial="empty-state"):
                yield Static(
                    "No agents yet.\n\n"
                    "F6 or '+ Add agent' to spawn one.\n"
                    "F9 to switch sessions.\n"
                    "F12 for help.",
                    id="empty-state",
                )
            yield DiffTray(id="diff-tray")
        yield Footer()

    async def on_mount(self) -> None:
        # One-time migration from pre-multi-session layout.
        self._sessions.migrate_legacy()
        info = self._sessions.get_or_create_default()
        self._session = self._sessions.open_session(info)
        self.query_one(Sidebar).set_session_name(info.name)

        # Load this session's pipeline, or seed from config if it's new.
        saved = self._session.load()
        if saved is not None:
            restored, skipped = restore_pipeline(
                self.pipeline, self._session, saved,
                config_role_names=set(self.config.roles),
                config_agent_names=set(self.config.agents),
            )
            if restored:
                self.notify(
                    f"resumed {restored} agent{'s' if restored != 1 else ''}"
                    f" from session '{info.name}'"
                    + (f" ({skipped} skipped)" if skipped else ""),
                    timeout=4,
                )
            else:
                seed_pipeline_from_config(self.pipeline, self._session)
        else:
            seed_pipeline_from_config(self.pipeline, self._session)

        switcher = self.query_one("#panes", ContentSwitcher)
        for node in self.pipeline.nodes:
            await self._mount_panel_for(node, switcher)
            node.seen = True

        await self.query_one(Sidebar).refresh_nodes()

        first = next(iter(self.pipeline.nodes), None)
        if first is not None:
            self._focus_node(first.spec.id)
        else:
            # No nodes: park focus on the sidebar so user can keyboard-nav.
            self.query_one(Sidebar).focus_list()

        # Tick status states for persistent panes based on PTY activity.
        self.set_interval(1.0, self._tick_status)

        if not self._mouse:
            self._write_mouse_seq(enable=False)

        if self._yolo:
            yolo_agents = sorted(
                name for name, r in self.config.agents.items() if r.yolo_args
            )
            self.notify(
                "⚠ --dangerously-skip-permissions ON for: "
                + (", ".join(yolo_agents) if yolo_agents else "(no agents have yolo_args)"),
                severity="warning",
                timeout=8,
            )

        # Persist the (possibly restored, possibly newly-seeded) state.
        self._save_session()

    async def _mount_panel_for(self, node: NodeState, switcher: ContentSwitcher) -> None:
        pane_id = self._pane_id(node.spec.id)
        if node.spec.mode == "persistent":
            recipe = self.config.agent_for_node(node.spec)
            command = self._yolo_extend(recipe.command, recipe)
            await switcher.mount(
                PtyPane(command, cwd=str(node.worktree.path), id=pane_id)
            )
        else:
            label = f"{node.spec.agent}" + (
                f" · {node.spec.role}" if node.spec.role else ""
            )
            await switcher.mount(
                OneShotPanel(node.spec.id, label, id=pane_id)
            )

    def _pane_id(self, node_id: str) -> str:
        return "pane-" + node_id.replace("/", "_").replace(".", "_")

    # --- focus management ---------------------------------------------------

    def _focus_node(self, node_id: str) -> None:
        try:
            node = self.pipeline.node(node_id)
        except KeyError:
            return
        switcher = self.query_one("#panes", ContentSwitcher)
        switcher.current = self._pane_id(node_id)
        self._current_node_id = node_id
        try:
            self.query_one(f"#{self._pane_id(node_id)}").focus()
        except Exception:
            pass
        self._refresh_diff_tray(node)

    def _refresh_diff_tray(self, node: NodeState | None = None) -> None:
        if node is None and self._current_node_id is not None:
            try:
                node = self.pipeline.node(self._current_node_id)
            except KeyError:
                node = None
        if node is None:
            return
        try:
            tray = self.query_one(DiffTray)
        except Exception:
            return
        tray.show_for(f"{node.spec.id}  ({node.status})", node.worktree.path)

    def on_sidebar_node_selected(self, message: Sidebar.NodeSelected) -> None:
        self._focus_node(message.node_id)

    def on_sidebar_add_requested(self, _message: Sidebar.AddRequested) -> None:
        self.action_add_agent()

    def action_add_agent(self) -> None:
        agents = sorted(self.config.agents.keys())
        roles = sorted(self.config.roles.keys())
        if not agents:
            self.notify("no agents configured", severity="error")
            return
        self.push_screen(
            SpawnPickerScreen(agents, roles),
            self._on_picker_dismissed,
        )

    def _on_picker_dismissed(self, result: dict | None) -> None:
        if not result:
            return
        asyncio.create_task(
            self._spawn_node(
                agent=result["agent"],
                role=result.get("role"),
                mode=result.get("mode", "persistent"),
            )
        )

    async def _spawn_node(
        self,
        *,
        agent: str,
        role: str | None,
        mode: str,
    ) -> str | None:
        if agent not in self.config.agents:
            self.notify(f"unknown agent: {agent!r}", severity="error")
            return None
        if role is not None and role not in self.config.roles:
            self.notify(f"unknown role: {role!r}", severity="error")
            return None
        base = role or agent
        node_id = base
        i = 2
        existing = {n.spec.id for n in self.pipeline.nodes}
        while node_id in existing:
            node_id = f"{base}-{i}"
            i += 1
        spec = NodeSpec(id=node_id, agent=agent, role=role, mode=mode)
        assert self._session is not None
        worktree = self.workspace.ensure_worktree_at(
            self._session.path_for(node_id),
            self._session.branch_for(node_id),
            node_id=node_id,
        )
        state = NodeState(spec=spec, worktree=worktree)
        self.pipeline.nodes.append(state)
        switcher = self.query_one("#panes", ContentSwitcher)
        await self._mount_panel_for(state, switcher)
        state.seen = True
        await self.query_one(Sidebar).refresh_nodes()
        self._focus_node(node_id)
        suffix = f" · {role}" if role else ""
        self.notify(f"spawned {node_id} ({agent}{suffix}, {mode})")
        self._save_session()
        return node_id

    def action_toggle_sidebar_focus(self) -> None:
        sidebar = self.query_one(Sidebar)
        if sidebar.has_focus_within and self._current_node_id is not None:
            self._focus_node(self._current_node_id)
        else:
            sidebar.focus_list()

    def action_toggle_diff(self) -> None:
        self.query_one(DiffTray).toggle_class("hidden")

    def action_toggle_mouse(self) -> None:
        """Toggle mouse tracking. Off lets the terminal handle native
        click-drag text selection; on lets the app receive clicks
        (sidebar items, etc.).
        """
        self._mouse = not self._mouse
        self._write_mouse_seq(enable=self._mouse)
        self.notify(
            f"mouse {'on' if self._mouse else 'off — select text natively'}",
            timeout=3,
        )

    def _save_session(self) -> None:
        """Persist current pipeline shape to this session's .json."""
        if self._session is None:
            return
        try:
            self._session.save(self.pipeline, self._current_node_id)
        except Exception:
            pass

    def _write_mouse_seq(self, *, enable: bool) -> None:
        # Standard SGR mouse modes Textual uses; toggling them all is
        # the safest way to coexist with whatever it set up.
        codes = ["?1000", "?1002", "?1003", "?1006"]
        suffix = "h" if enable else "l"
        seq = "".join(f"\x1b[{c}{suffix}" for c in codes)
        try:
            sys.stdout.write(seq)
            sys.stdout.flush()
        except Exception:
            pass

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_open_session_picker(self) -> None:
        rows = []
        current = self._sessions.current_id()
        for info in self._sessions.list():
            # Count nodes in each session by reading its session.json directly.
            sess = self._sessions.open_session(info)
            saved = sess.load() or []
            rows.append(_SessionRow(
                id=info.id,
                name=info.name,
                node_count=len(saved),
                is_current=(info.id == current),
            ))
        self.push_screen(
            SessionPickerScreen(rows),
            self._on_session_picker_dismissed,
        )

    def _on_session_picker_dismissed(self, result: dict | None) -> None:
        if not result:
            return
        action = result.get("action")
        if action == "switch":
            asyncio.create_task(self._switch_session(result["id"]))
        elif action == "create":
            asyncio.create_task(self._create_and_switch_session(result["name"]))
        elif action == "delete":
            asyncio.create_task(self._delete_session(result["id"]))

    def on_sidebar_session_picker_requested(
        self, _message: Sidebar.SessionPickerRequested
    ) -> None:
        self.action_open_session_picker()

    async def _switch_session(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            self.notify(f"unknown session: {session_id}", severity="error")
            return
        if self._session is not None and info.id == self._session.info.id:
            return  # already active
        await self._teardown_current_session()
        self._sessions.set_current(info.id)
        self._session = self._sessions.open_session(info)
        self.query_one(Sidebar).set_session_name(info.name)

        saved = self._session.load()
        if saved:
            restore_pipeline(
                self.pipeline, self._session, saved,
                config_role_names=set(self.config.roles),
                config_agent_names=set(self.config.agents),
            )
        else:
            # New empty session.
            pass

        switcher = self.query_one("#panes", ContentSwitcher)
        for node in self.pipeline.nodes:
            await self._mount_panel_for(node, switcher)
            node.seen = True
        await self.query_one(Sidebar).refresh_nodes()
        first = next(iter(self.pipeline.nodes), None)
        if first is not None:
            self._focus_node(first.spec.id)
        else:
            self._current_node_id = None
            switcher.current = "empty-state"
        self.notify(f"switched to session '{info.name}'")
        self._save_session()

    async def _create_and_switch_session(self, name: str) -> None:
        info = self._sessions.create(name, as_current=False)
        await self._switch_session(info.id)

    async def _delete_session(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            return
        if self._session is not None and info.id == self._session.info.id:
            self.notify("can't delete the active session", severity="warning")
            return
        self._sessions.delete(info.id)
        self.notify(f"deleted session '{info.name}'")

    async def _teardown_current_session(self) -> None:
        """Save the current session, then unmount its panes and clear pipeline."""
        self._save_session()
        switcher = self.query_one("#panes", ContentSwitcher)
        switcher.current = "empty-state"
        # Remove all per-node widgets (PtyPane or OneShotPanel).
        for node in list(self.pipeline.nodes):
            try:
                pane = self.query_one(f"#{self._pane_id(node.spec.id)}")
                await pane.remove()
            except Exception:
                pass
        self.pipeline.reset()
        self._current_node_id = None

    async def _tick_status(self) -> None:
        """Update persistent-node statuses based on PTY activity."""
        now = time.monotonic()
        changed = False
        for node in self.pipeline.nodes:
            if node.spec.mode != "persistent":
                continue
            try:
                pane = self.query_one(f"#{self._pane_id(node.spec.id)}", PtyPane)
            except Exception:
                continue
            if not pane.is_alive and pane.exit_code is not None:
                new_status = "exited"
            elif pane.last_byte_at == 0:
                new_status = "idle"
            elif now - pane.last_byte_at < self._ACTIVITY_WINDOW:
                new_status = "running"
            else:
                new_status = "idle"
            if node.status != new_status:
                node.status = new_status
                changed = True
        if changed:
            await self.query_one(Sidebar).refresh_nodes()

    # --- handoff (F2 / palette) --------------------------------------------

    async def action_handoff(self) -> None:
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        source = self.pipeline.node(self._current_node_id)
        target = self.pipeline.next_after(source.spec.id)
        if target is None:
            self.notify(
                f"{source.spec.id} is the last node — use :handoff <node> to reroute",
                severity="warning",
            )
            return
        await self._do_handoff(source, target)

    async def _do_handoff(self, source: NodeState, target: NodeState) -> None:
        try:
            result = await asyncio.to_thread(do_handoff, self.workspace, source, target)
        except Exception as e:
            self.notify(f"handoff failed: {e}", severity="error", timeout=8)
            return
        if not result.ok:
            self.notify(result.message, severity="warning", timeout=6)
            return
        self.notify(result.message, timeout=4)
        self._save_session()

        if target.spec.mode == "persistent":
            prompt = self.config.prompt_for_node(target.spec)
            if prompt:
                try:
                    pane = self.query_one(f"#{self._pane_id(target.spec.id)}", PtyPane)
                    self._inject_prompt(pane, prompt)
                except Exception:
                    pass
            self._focus_node(target.spec.id)
        else:
            self._focus_node(target.spec.id)
            asyncio.create_task(self._run_one_shot(target))

        await self.query_one(Sidebar).refresh_nodes()

    def _inject_prompt(self, pane: PtyPane, prompt: str) -> None:
        if not pane.is_alive:
            return
        proc = pane._proc
        if proc is None:
            return
        line = " ".join(prompt.split())
        try:
            os.write(proc.fd, line.encode("utf-8"))
            os.write(proc.fd, b"\r")
        except OSError:
            pass

    # --- one-shot execution -------------------------------------------------

    async def _run_one_shot(self, node: NodeState) -> None:
        recipe = self.config.agent_for_node(node.spec)
        if not recipe.one_shot:
            self.notify(
                f"agent {recipe.name!r} has no one_shot recipe — set "
                f"`one_shot = [...]` in config",
                severity="error",
            )
            node.status = "blocked"
            await self.query_one(Sidebar).refresh_nodes()
            return
        prompt = self.config.prompt_for_node(node.spec)
        cmd = [a.replace("{prompt}", prompt) for a in self._yolo_extend(recipe.one_shot, recipe)]

        panel = self.query_one(f"#{self._pane_id(node.spec.id)}", OneShotPanel)
        panel.begin_run(cmd)
        node.status = "running"
        await self.query_one(Sidebar).refresh_nodes()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(node.worktree.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            panel.append(f"\n[error: {e}]\n")
            panel.end_run(127)
            node.status = "blocked"
            await self.query_one(Sidebar).refresh_nodes()
            return

        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            panel.append(chunk.decode(errors="replace"))
        rc = await proc.wait()
        panel.end_run(rc)
        node.status = "ready" if rc == 0 else "blocked"
        await self.query_one(Sidebar).refresh_nodes()
        self._refresh_diff_tray(node)

    # --- editor interjection (F3) ------------------------------------------

    def action_edit_artifact(self) -> None:
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        node = self.pipeline.node(self._current_node_id)
        files = self._pending_files(node)
        if not files:
            self.notify("no pending files to edit", severity="warning")
            return
        editor_cmd = os.environ.get("EDITOR") or "vi"
        cmd = shlex.split(editor_cmd) + files
        try:
            with self.suspend():
                subprocess.run(cmd, cwd=str(node.worktree.path), check=False)
        except Exception as e:
            self.notify(f"editor failed: {e}", severity="error", timeout=8)
            return
        self._refresh_diff_tray(node)

    def _pending_files(self, node: NodeState) -> list[str]:
        cp = git(
            ["ls-files", "--modified", "--others", "--exclude-standard"],
            cwd=node.worktree.path, check=False,
        )
        files = [
            line for line in cp.stdout.decode(errors="replace").splitlines()
            if line.strip()
        ]
        return files

    # --- command palette (F1) -----------------------------------------------

    def action_open_palette(self) -> None:
        self.push_screen(CommandPaletteScreen(), self._on_palette_dismissed)

    def _on_palette_dismissed(self, command: str | None) -> None:
        if command:
            asyncio.create_task(self._dispatch_command(command))

    async def _dispatch_command(self, raw: str) -> None:
        try:
            parts = shlex.split(raw)
        except ValueError as e:
            self.notify(f"bad command: {e}", severity="error")
            return
        if not parts:
            return
        verb, args = parts[0], parts[1:]
        handler = {
            "spawn":          self._cmd_spawn,
            "handoff":        self._cmd_handoff,
            "edit":           self._cmd_edit,
            "rerun":          self._cmd_rerun,
            "resume":         self._cmd_resume,
            "swap-agent":     self._cmd_swap_agent,
            "swap-role":      self._cmd_swap_role,
            "reset-session":  self._cmd_reset_session,
            "session":        self._cmd_session,
            "new-session":    self._cmd_new_session,
            "switch-session": self._cmd_switch_session,
            "quit":           self._cmd_quit,
        }.get(verb)
        if handler is None:
            self.notify(f"unknown command: {verb}", severity="error")
            return
        await handler(args)

    async def _cmd_spawn(self, args: list[str]) -> None:
        """`:spawn` with no args opens the picker. With args, parses them.

        Forms:
          spawn                          → open picker modal
          spawn <agent>                  → agent only, no role, persistent
          spawn <agent> <role>           → agent + role, persistent
          spawn -o <agent> [<role>]      → one-shot
        """
        if not args:
            self.action_add_agent()
            return
        mode = "persistent"
        positional: list[str] = []
        for a in args:
            if a in ("-o", "--one-shot"):
                mode = "one-shot"
            else:
                positional.append(a)
        if len(positional) == 1:
            agent, role = positional[0], None
        elif len(positional) == 2:
            agent, role = positional[0], positional[1]
        else:
            self.notify(
                "usage: spawn [-o] <agent> [<role>]  (or `spawn` with no args)",
                severity="error",
            )
            return
        await self._spawn_node(agent=agent, role=role, mode=mode)

    async def _cmd_swap_agent(self, args: list[str]) -> None:
        if len(args) != 1:
            self.notify("usage: swap-agent <agent>", severity="error")
            return
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        new_agent = args[0]
        if new_agent not in self.config.agents:
            self.notify(f"unknown agent: {new_agent!r}", severity="error")
            return
        node = self.pipeline.node(self._current_node_id)
        if node.spec.agent == new_agent:
            self.notify(f"already {new_agent}")
            return
        node.spec = NodeSpec(
            id=node.spec.id, agent=new_agent, role=node.spec.role, mode=node.spec.mode
        )
        # Tear down and re-mount the pane / panel.
        switcher = self.query_one("#panes", ContentSwitcher)
        pane_id = self._pane_id(node.spec.id)
        try:
            old = self.query_one(f"#{pane_id}")
            await old.remove()
        except Exception:
            pass
        await self._mount_panel_for(node, switcher)
        node.seen = True
        node.status = "idle"
        await self.query_one(Sidebar).refresh_nodes()
        self._focus_node(node.spec.id)
        self.notify(f"swapped {node.spec.id} → agent {new_agent}")
        self._save_session()

    async def _cmd_swap_role(self, args: list[str]) -> None:
        if len(args) != 1:
            self.notify("usage: swap-role <role|none>", severity="error")
            return
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        raw = args[0]
        new_role: str | None
        if raw in ("none", "blank", "_blank"):
            new_role = None
        else:
            new_role = raw
            if new_role not in self.config.roles:
                self.notify(f"unknown role: {new_role!r}", severity="error")
                return
        node = self.pipeline.node(self._current_node_id)
        node.spec = NodeSpec(
            id=node.spec.id, agent=node.spec.agent, role=new_role, mode=node.spec.mode
        )
        await self.query_one(Sidebar).refresh_nodes()
        self.notify(
            f"swapped {node.spec.id} → role {new_role or 'blank'}"
        )
        self._save_session()

    async def _cmd_handoff(self, args: list[str]) -> None:
        if self._current_node_id is None:
            self.notify("no node focused", severity="warning")
            return
        source = self.pipeline.node(self._current_node_id)
        if not args:
            target = self.pipeline.next_after(source.spec.id)
            if target is None:
                self.notify(f"{source.spec.id} has no next node", severity="warning")
                return
        else:
            try:
                target = self.pipeline.node(args[0])
            except KeyError:
                self.notify(f"unknown node: {args[0]!r}", severity="error")
                return
            if target.spec.id == source.spec.id:
                self.notify("can't hand off to self", severity="warning")
                return
        await self._do_handoff(source, target)

    async def _cmd_edit(self, _args: list[str]) -> None:
        self.action_edit_artifact()

    async def _cmd_rerun(self, _args: list[str]) -> None:
        if self._current_node_id is None:
            return
        node = self.pipeline.node(self._current_node_id)
        if node.spec.mode != "one-shot":
            self.notify("rerun is only for one-shot nodes", severity="warning")
            return
        asyncio.create_task(self._run_one_shot(node))

    async def _cmd_quit(self, _args: list[str]) -> None:
        self.exit()

    async def _cmd_session(self, _args: list[str]) -> None:
        """`:session` opens the session picker (same as F9)."""
        self.action_open_session_picker()

    async def _cmd_new_session(self, args: list[str]) -> None:
        if not args:
            self.notify("usage: new-session <name>", severity="error")
            return
        await self._create_and_switch_session(" ".join(args))

    async def _cmd_switch_session(self, args: list[str]) -> None:
        if not args:
            self.notify("usage: switch-session <id-or-name>", severity="error")
            return
        target = args[0]
        info = self._sessions.get(target)
        if info is None:
            for s in self._sessions.list():
                if s.name == target:
                    info = s
                    break
        if info is None:
            self.notify(f"unknown session: {target}", severity="error")
            return
        await self._switch_session(info.id)

    async def _cmd_resume(self, args: list[str]) -> None:
        """Resume conversation for a node (or the focused one).

        Tears down the current pane and respawns the agent with its
        configured resume_args (e.g. `claude --continue`). Only meaningful
        for persistent nodes whose agent declares resume_args.
        """
        node_id = args[0] if args else self._current_node_id
        if node_id is None:
            self.notify("no node focused", severity="warning")
            return
        try:
            node = self.pipeline.node(node_id)
        except KeyError:
            self.notify(f"unknown node: {node_id!r}", severity="error")
            return
        if node.spec.mode != "persistent":
            self.notify("resume is only for persistent nodes", severity="warning")
            return
        recipe = self.config.agent_for_node(node.spec)
        if not recipe.resume_args:
            self.notify(
                f"agent {recipe.name!r} has no resume_args — set "
                f"`resume_args = [...]` in config",
                severity="warning",
            )
            return
        switcher = self.query_one("#panes", ContentSwitcher)
        pane_id = self._pane_id(node.spec.id)
        try:
            old = self.query_one(f"#{pane_id}")
            await old.remove()
        except Exception:
            pass
        # Spawn with command + resume_args (plus yolo if active).
        cmd = list(recipe.command) + list(recipe.resume_args)
        if self._yolo and recipe.yolo_args:
            cmd.extend(recipe.yolo_args)
        await switcher.mount(
            PtyPane(cmd, cwd=str(node.worktree.path), id=pane_id)
        )
        node.status = "idle"
        self._focus_node(node.spec.id)
        self.notify(f"resumed {node.spec.id}")
        self._save_session()

    async def _cmd_reset_session(self, _args: list[str]) -> None:
        """Clear the current session's saved state. Worktrees + branches kept."""
        if self._session is None:
            return
        try:
            self._session.path.unlink()
        except FileNotFoundError:
            pass
        self.notify(
            f"session '{self._session.info.name}' state cleared; "
            "relaunch to start fresh",
            timeout=5,
        )


# --- entrypoints -----------------------------------------------------------

def run_term(
    workspace_root: Path | None = None,
    *,
    yolo: bool = False,
    mouse: bool | None = None,
) -> None:
    from term.config import load_config
    start = workspace_root if workspace_root is not None else Path.cwd()
    workspace = Workspace.discover(start)
    config = load_config(workspace.root)
    TermApp(config, workspace, yolo=yolo, mouse=mouse).run()


def run_spike(command: Sequence[str] | None, cwd: str | None = None) -> None:
    cmd = list(command) if command else [os.environ.get("SHELL", "/bin/bash")]
    SpikeApp(cmd, cwd=cwd).run()
