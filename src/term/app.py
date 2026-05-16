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
from term.mood import MoodTracker
from term.session import (
    Session,
    SessionInfo,
    SessionManager,
    restore_pipeline,
    seed_pipeline_from_config,
    slugify as _slugify,
)
from term.subagent import (
    MAX_CONCURRENT_SUBS,
    SubTask,
    build_subtask_command,
    new_id as _new_subtask_id,
    render_delivery,
    run_subtask,
)
from term.team_doc import write_team_docs
from term.widgets.session_picker import SessionPickerScreen, _SessionRow
from term.widgets.command_palette import CommandPaletteScreen
from term.widgets.confirm_screen import ConfirmScreen
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
        self._mood = MoodTracker()
        self._last_mood: str = "neutral"
        # In-flight one-shot sub-agents spawned by other agents.
        # Sidebar reads this each refresh; _tick_spawn drains the queue.
        self._subtasks: list[SubTask] = []

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
        sidebar = self.query_one(Sidebar)
        sidebar.set_session_name(info.name)
        sidebar.subtasks = self._subtasks

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

        # Refresh AGENTS.md BEFORE spawning panes so agents read an
        # up-to-date team roster at startup.
        self._refresh_team_docs()

        switcher = self.query_one("#panes", ContentSwitcher)
        for node in self.pipeline.nodes:
            # Auto-resume previously-seen persistent nodes (e.g.
            # `claude --continue`) so the conversation picks up where
            # the user left off. Brand-new seeded nodes have seen=False.
            await self._mount_panel_for(node, switcher, resume=node.seen)
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
        # Tick the mood / avatar so it decays and re-renders.
        self.set_interval(2.0, self._tick_mood)
        # Tick the mailbox to deliver inter-agent messages.
        self.set_interval(1.5, self._tick_mail)
        # Tick the spawn queue to launch / clean up sub-agents.
        self.set_interval(1.5, self._tick_spawn)
        # Ensure mailbox + spawn dirs exist for the current session.
        if self._session is not None:
            self._session.ensure()

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

    async def _mount_panel_for(
        self,
        node: NodeState,
        switcher: ContentSwitcher,
        *,
        resume: bool = False,
    ) -> None:
        pane_id = self._pane_id(node.spec.id)
        if node.spec.mode == "persistent":
            recipe = self.config.agent_for_node(node.spec)
            base = recipe.command
            resumed = resume and bool(recipe.resume_args)
            if resumed:
                base = tuple(recipe.command) + tuple(recipe.resume_args)
            command = self._yolo_extend(base, recipe)
            image_dir = self._session.image_dir if self._session else None
            await switcher.mount(
                PtyPane(
                    command,
                    cwd=str(node.worktree.path),
                    image_dir=image_dir,
                    id=pane_id,
                )
            )
            # Stamp so the watchdog can detect an immediate exit (e.g.
            # `claude --continue` when nothing to continue) and fall
            # back to a fresh spawn.
            node.resumed_at = time.monotonic() if resumed else None
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

    def on_sidebar_task_selected(
        self, message: Sidebar.TaskSelected,
    ) -> None:
        """Open the sub-task inspector for the clicked task row."""
        for sub in self._subtasks:
            if sub.id == message.task_id:
                from term.widgets.subtask_inspector import SubTaskInspectorScreen
                self.push_screen(SubTaskInspectorScreen(sub))
                return

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
                display_name=result.get("name"),
            )
        )

    async def _spawn_node(
        self,
        *,
        agent: str,
        role: str | None,
        mode: str,
        display_name: str | None = None,
    ) -> str | None:
        if agent not in self.config.agents:
            self.notify(f"unknown agent: {agent!r}", severity="error")
            return None
        if role is not None and role not in self.config.roles:
            self.notify(f"unknown role: {role!r}", severity="error")
            return None
        # Pick an id. If a display name was provided, slugify it; otherwise
        # fall back to role/agent.
        if display_name:
            base = _slugify(display_name)
        else:
            base = role or agent
        node_id = base
        i = 2
        existing = {n.spec.id for n in self.pipeline.nodes}
        while node_id in existing:
            node_id = f"{base}-{i}"
            i += 1
        spec = NodeSpec(
            id=node_id, agent=agent, role=role, mode=mode,
            display_name=display_name,
        )
        assert self._session is not None
        worktree = self.workspace.ensure_worktree_at(
            self._session.path_for(node_id),
            self._session.branch_for(node_id),
            node_id=node_id,
        )
        state = NodeState(spec=spec, worktree=worktree)
        self.pipeline.nodes.append(state)
        # Refresh AGENTS.md in ALL worktrees BEFORE the new pane mounts,
        # so the new agent's startup read of AGENTS.md picks up the full
        # team roster. (Existing peers won't re-read; they're briefed in
        # the conversation below.)
        self._refresh_team_docs()
        switcher = self.query_one("#panes", ContentSwitcher)
        await self._mount_panel_for(state, switcher)
        state.seen = True
        await self.query_one(Sidebar).refresh_nodes()
        self._focus_node(node_id)
        suffix = f" · {role}" if role else ""
        self.notify(f"spawned {spec.display} ({agent}{suffix}, {mode})")
        self._save_session()
        # Auto-introduce the new node + tell existing peers. Best-effort;
        # short delay so PTYs are ready to receive.
        if state.spec.mode == "persistent":
            asyncio.create_task(self._auto_introduce(state))
        return node_id

    async def _auto_introduce(self, new_node: NodeState) -> None:
        """After a new node spawns, brief it about peers and announce it to them."""
        # Give the new pane a moment to finish its CLI startup.
        await asyncio.sleep(1.5)
        peers = [
            n for n in self.pipeline.nodes
            if n.spec.id != new_node.spec.id and n.spec.mode == "persistent"
        ]
        if not peers:
            return
        # Brief the new node on the team + mailbox convention.
        self._inject_message_to_node(
            target_node_id=new_node.spec.id,
            content=self._render_brief(new_node, peers),
            sender="you",
        )
        # Announce the new arrival to each existing peer (one short line each).
        for peer in peers:
            notice = (
                f"New teammate just joined the session: "
                f"{new_node.spec.display} (id: {new_node.spec.id}). "
                f"You can message them via "
                f"../../mail/{peer.spec.id}__to__{new_node.spec.id}.txt."
            )
            self._inject_message_to_node(
                target_node_id=peer.spec.id, content=notice, sender="you",
            )

    def _refresh_team_docs(self) -> None:
        write_team_docs(self.pipeline.nodes)

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
            self._confirm_delete_session(result["id"])

    def _confirm_delete_session(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            return
        # Build a body describing what will be destroyed.
        sess = self._sessions.open_session(info)
        saved = sess.load() or []
        worktree_dir = self.workspace.root / info.worktree_root
        wt_count = (
            sum(1 for c in worktree_dir.iterdir() if (c / ".git").exists())
            if worktree_dir.exists() else len(saved)
        )
        body = (
            f"Delete session '{info.name}'?\n\n"
            f"Removes {wt_count} agent worktree(s) and their branches.\n"
            f"Any uncommitted changes in those worktrees will be lost.\n\n"
            f"Other sessions in this workspace are unaffected."
        )

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                asyncio.create_task(self._delete_session(session_id))

        self.push_screen(
            ConfirmScreen(f"Delete '{info.name}'?", body),
            on_confirm,
        )

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
        self._session.ensure()
        # Fresh mood per session.
        self._mood = MoodTracker()
        self._last_mood = "neutral"
        sidebar = self.query_one(Sidebar)
        sidebar.set_session_name(info.name)
        sidebar.set_session_mood("neutral")
        # Subtask list is per-app; bind here in case the sidebar was
        # mounted before the list was created (defensive — no-op if
        # they're already the same reference).
        sidebar.subtasks = self._subtasks
        # Ensure mailbox + spawn dirs exist for the new session.
        self._session.ensure()

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

        # Refresh AGENTS.md BEFORE spawning panes so agents read an
        # up-to-date team roster at startup.
        self._refresh_team_docs()

        switcher = self.query_one("#panes", ContentSwitcher)
        for node in self.pipeline.nodes:
            await self._mount_panel_for(node, switcher, resume=node.seen)
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
        # Cancel any in-flight sub-tasks — they reference worktrees in
        # the outgoing session and their senders won't be reachable.
        for sub in self._subtasks:
            if sub.task is not None and not sub.task.done():
                sub.task.cancel()
        self._subtasks.clear()
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

    def on_pty_pane_user_line_submitted(
        self, message: PtyPane.UserLineSubmitted
    ) -> None:
        """A user submitted a line into a pane → feed mood tracker."""
        self._mood.observe_user_text(message.text)
        self._refresh_mood_display()

    def on_pty_pane_image_captured(
        self, message: PtyPane.ImageCaptured,
    ) -> None:
        """An OSC 1337 inline image was extracted from a pane's output.

        Pyte+Textual can't render inline images, so we saved it to disk.
        Surface the path so the user can open it externally.
        """
        self.notify(
            f"image captured → {message.path}",
            timeout=8,
        )

    def _tick_mood(self) -> None:
        self._refresh_mood_display()

    def _tick_mail(self) -> None:
        """Pick up inter-agent messages dropped into the session's mail dir."""
        if self._session is None:
            return
        for target, content, sender in self._session.drain_mail():
            if not content:
                continue
            self._inject_message_to_node(
                target_node_id=target,
                content=content,
                sender=sender or "unknown",
            )

    def _tick_spawn(self) -> None:
        """Pick up sub-task spawn requests and launch / reap them."""
        if self._session is None:
            return
        for sender, payload, path in self._session.drain_spawn_requests():
            try:
                self._dispatch_spawn_request(sender, payload, path)
            except Exception as e:
                # Anything we didn't catch upstream — surface it and
                # remove the source file so we don't loop forever.
                self.notify(
                    f"sub-task: dispatch failed ({e})", severity="error",
                )
                try:
                    path.unlink()
                except OSError:
                    pass
        # Drop done/cancelled subs from the tray after they've delivered.
        before = len(self._subtasks)
        self._subtasks = [
            s for s in self._subtasks
            if s.status in {"queued", "running"}
        ]
        # Refresh the sidebar each tick while subs are live so the
        # "elapsed time" cell ticks forward, and on any roster change.
        if self._subtasks or len(self._subtasks) != before:
            asyncio.create_task(self.query_one(Sidebar).refresh_nodes())

    def _dispatch_spawn_request(
        self, sender: str, payload: dict, source: Path,
    ) -> None:
        """Validate and launch a single spawn request from the queue."""
        assert self._session is not None
        # Always delete the source file first so a re-tick can't double-fire.
        try:
            source.unlink()
        except OSError:
            pass

        if not sender:
            self.notify(
                "sub-task: spawn file is missing the '<sender>__spawn__' prefix",
                severity="warning",
            )
            return
        if "_error" in payload:
            self.notify(
                f"sub-task from {sender}: {payload['_error']}",
                severity="error",
            )
            return

        sender_node = self._resolve_node(sender)
        if sender_node is None:
            self.notify(
                f"sub-task: unknown requester {sender!r}", severity="warning",
            )
            return

        agent_name = payload.get("agent")
        prompt_text = payload.get("prompt")
        if not agent_name or not isinstance(agent_name, str):
            self._fail_subtask_delivery(
                sender_node.spec.id,
                "spawn request missing required field: agent",
            )
            return
        if not prompt_text or not isinstance(prompt_text, str):
            self._fail_subtask_delivery(
                sender_node.spec.id,
                "spawn request missing required field: prompt",
            )
            return
        recipe = self.config.agents.get(agent_name)
        if recipe is None:
            self._fail_subtask_delivery(
                sender_node.spec.id,
                f"spawn request: unknown agent {agent_name!r}",
            )
            return

        # Role wrap: if requested, prepend the role's prompt_template.
        role = payload.get("role")
        if role:
            role_obj = self.config.roles.get(role)
            if role_obj is None:
                self._fail_subtask_delivery(
                    sender_node.spec.id,
                    f"spawn request: unknown role {role!r}",
                )
                return
            if role_obj.prompt_template:
                prompt_text = role_obj.prompt_template + "\n\n" + prompt_text

        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            self._fail_subtask_delivery(
                sender_node.spec.id, "spawn request: model must be a string",
            )
            return

        # Concurrency cap.
        live = sum(1 for s in self._subtasks if s.status in {"queued", "running"})
        if live >= MAX_CONCURRENT_SUBS:
            self._fail_subtask_delivery(
                sender_node.spec.id,
                f"spawn request rejected: {live} subs already running "
                f"(cap {MAX_CONCURRENT_SUBS})",
            )
            return

        cmd, err = build_subtask_command(
            recipe, model=model, yolo=self._yolo, prompt=prompt_text,
        )
        if err:
            self._fail_subtask_delivery(sender_node.spec.id, err)
            return

        display = (payload.get("name") or "").strip() or _new_subtask_id()[:6]
        sub = SubTask(
            id=_new_subtask_id(),
            sender_id=sender_node.spec.id,
            agent_name=agent_name,
            model=model,
            display=display,
            prompt=prompt_text,
            cwd=sender_node.worktree.path,
            started_at=time.monotonic(),
        )
        self._subtasks.append(sub)
        sub.task = asyncio.create_task(self._supervise_subtask(sub, cmd))
        asyncio.create_task(self.query_one(Sidebar).refresh_nodes())
        self.notify(
            f"sub-task: {sender_node.spec.display} → {agent_name}"
            + (f" ({model})" if model else "")
            + f" · {display}",
            timeout=3,
        )

    async def _supervise_subtask(self, sub: SubTask, cmd: list[str]) -> None:
        """Run the sub to completion and deliver the result to its sender."""
        try:
            await run_subtask(sub, cmd)
        except asyncio.CancelledError:
            # Sub was cancelled (e.g. session switch). Still try to deliver
            # what we have so the sender knows why it never heard back.
            sub.status = "cancelled"
            raise
        finally:
            self._deliver_subtask_result(sub)
            try:
                await self.query_one(Sidebar).refresh_nodes()
            except Exception:
                pass

    def _deliver_subtask_result(self, sub: SubTask) -> None:
        """Inject the sub's result into the requester's pane (or notify)."""
        body = render_delivery(sub)
        node = self._resolve_node(sub.sender_id)
        if node is None or node.spec.mode != "persistent":
            # Requester is gone or one-shot itself — surface in the UI.
            self.notify(
                f"sub-task {sub.display} done (no live requester to deliver to)",
                timeout=5,
            )
            return
        self._inject_message_to_node(
            target_node_id=sub.sender_id, content=body, sender=sub.agent_name,
        )

    def _fail_subtask_delivery(self, sender_id: str, reason: str) -> None:
        """Tell the requester their spawn request didn't fly."""
        body = f"[sub-task rejected]\n{reason}\n"
        node = self._resolve_node(sender_id)
        if node is not None and node.spec.mode == "persistent":
            self._inject_message_to_node(
                target_node_id=sender_id, content=body, sender="term",
            )
        else:
            self.notify(f"sub-task ({sender_id}): {reason}", severity="warning")

    def _resolve_node(self, ref: str) -> NodeState | None:
        """Find a node by id, display name, or slugified display name."""
        try:
            return self.pipeline.node(ref)
        except KeyError:
            pass
        ref_lower = ref.lower()
        ref_slug = _slugify(ref)
        for n in self.pipeline.nodes:
            if (n.spec.display_name or "").lower() == ref_lower:
                return n
            if n.spec.id == ref_slug:
                return n
        return None

    def _inject_message_to_node(
        self, *, target_node_id: str, content: str, sender: str,
    ) -> bool:
        """Bracketed-paste a message into the named pane's PTY."""
        node = self._resolve_node(target_node_id)
        if node is None:
            self.notify(
                f"can't deliver to {target_node_id!r}: no such node in session",
                severity="warning",
            )
            return False
        target_node_id = node.spec.id
        if node.spec.mode != "persistent":
            self.notify(
                f"can't deliver to {node.spec.display}: not a persistent agent",
                severity="warning",
            )
            return False
        try:
            pane = self.query_one(f"#{self._pane_id(node.spec.id)}", PtyPane)
        except Exception:
            return False
        if not pane.is_alive or pane._proc is None:
            return False
        wrapped = f"[From {sender}]: {content}"
        # Always auto-submit (send Enter after the bracketed paste) so the
        # receiving agent actually processes the message without you having
        # to manually hit Enter in their pane.
        data = (
            b"\x1b[200~"
            + wrapped.encode("utf-8", errors="replace")
            + b"\x1b[201~"
            + b"\r"
        )
        try:
            os.write(pane._proc.fd, data)
        except OSError:
            return False
        self.notify(f"delivered to {node.spec.display} (from {sender})", timeout=3)
        return True

    def _refresh_mood_display(self) -> None:
        mood = self._mood.current_mood()
        if mood == self._last_mood:
            return
        self._last_mood = mood
        try:
            self.query_one(Sidebar).set_session_mood(mood)
        except Exception:
            pass

    # If a resumed pane (spawned with resume_args) exits within this
    # window with a non-zero code, treat it as "nothing to resume" and
    # respawn fresh. Conservative: real conversations stay alive much
    # longer than this; legitimate fast crashes shouldn't be masked
    # because we only fall back once per node per spawn.
    _RESUME_FAILURE_WINDOW = 3.0

    async def _tick_status(self) -> None:
        """Update persistent-node statuses based on PTY activity."""
        now = time.monotonic()
        changed = False
        fallback: list[NodeState] = []
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
                if new_status == "exited" and (
                    pane.exit_code is not None and pane.exit_code != 0
                ):
                    # Auto-resume failed (e.g. claude --continue with no
                    # prior conversation in cwd). Fall back to a fresh
                    # spawn so the user doesn't see a dead pane.
                    if (
                        node.resumed_at is not None
                        and (now - node.resumed_at) < self._RESUME_FAILURE_WINDOW
                    ):
                        fallback.append(node)
                    else:
                        self._mood.observe_pane_exit(pane.exit_code)
                node.status = new_status
                changed = True
            # If the resume window has elapsed and the pane is still
            # alive, clear the stamp so a later real exit doesn't
            # incorrectly trigger a fallback.
            elif (
                node.resumed_at is not None
                and (now - node.resumed_at) >= self._RESUME_FAILURE_WINDOW
            ):
                node.resumed_at = None
        if changed:
            await self.query_one(Sidebar).refresh_nodes()
        for node in fallback:
            asyncio.create_task(self._fallback_to_fresh_spawn(node))
        self._refresh_mood_display()

    async def _fallback_to_fresh_spawn(self, node: NodeState) -> None:
        """Resume failed (pane exited fast). Respawn without resume_args."""
        node.resumed_at = None
        switcher = self.query_one("#panes", ContentSwitcher)
        pane_id = self._pane_id(node.spec.id)
        try:
            old = self.query_one(f"#{pane_id}")
            await old.remove()
        except Exception:
            pass
        await self._mount_panel_for(node, switcher, resume=False)
        node.status = "idle"
        await self.query_one(Sidebar).refresh_nodes()
        if self._current_node_id == node.spec.id:
            self._focus_node(node.spec.id)
        self.notify(
            f"{node.spec.display}: no prior conversation to resume — "
            f"started fresh",
            timeout=5,
        )

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
        self._mood.observe_handoff_success()
        self._refresh_mood_display()
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
            "fresh":          self._cmd_fresh,
            "tell":           self._cmd_tell,
            "brief":          self._cmd_brief,
            "swap-agent":     self._cmd_swap_agent,
            "swap-role":      self._cmd_swap_role,
            "reset-session":  self._cmd_reset_session,
            "session":        self._cmd_session,
            "new-session":    self._cmd_new_session,
            "switch-session": self._cmd_switch_session,
            "images":         self._cmd_images,
            "open-image":     self._cmd_open_image,
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

    async def _cmd_brief(self, args: list[str]) -> None:
        """`:brief [<node>|all]` — prime an agent to use peer-to-peer comms.

        Ambient AGENTS.md alone often doesn't get an agent to proactively
        message peers. This injects a direct, in-conversation instruction
        listing the peers and the mailbox convention so the agent knows
        it can act on it. Run once per agent at the start of a session.
        """
        if args and args[0] == "all":
            targets = [n for n in self.pipeline.nodes if n.spec.mode == "persistent"]
        elif args:
            node = self._resolve_node(args[0])
            if node is None:
                self.notify(f"unknown node: {args[0]}", severity="error")
                return
            targets = [node]
        elif self._current_node_id is not None:
            targets = [self.pipeline.node(self._current_node_id)]
        else:
            self.notify("usage: brief [<node>|all]", severity="error")
            return
        sent = 0
        for target in targets:
            others = [n for n in self.pipeline.nodes if n.spec.id != target.spec.id]
            if not others or target.spec.mode != "persistent":
                continue
            msg = self._render_brief(target, others)
            ok = self._inject_message_to_node(
                target_node_id=target.spec.id, content=msg, sender="you",
            )
            if ok:
                sent += 1
        self.notify(f"briefed {sent} agent{'s' if sent != 1 else ''}", timeout=3)

    def _render_brief(self, me: NodeState, others: list[NodeState]) -> str:
        names = ", ".join(
            f"{n.spec.display} (id: {n.spec.id})" for n in others
        )
        return (
            f"You have peer agents in this term session you can coordinate "
            f"with directly: {names}. To send a message to one, write a text "
            f"file at ../../mail/{me.spec.id}__to__<their-id>.txt with the "
            f"body of your message. Term picks it up within 1.5 seconds and "
            f"delivers it as a paste to their terminal prefixed "
            f"[From {me.spec.id}]:. Their replies arrive at your prompt the "
            f"same way. Coordinate with them whenever you need information "
            f"or want to delegate — you do not need to ask me first."
        )

    async def _cmd_tell(self, args: list[str]) -> None:
        """`:tell <node> <message>` — relay a message into another pane.

        Sender defaults to 'you' (the human at the console). If the focused
        node is persistent, its id is used as the sender so the target can
        tell who's talking.
        """
        if len(args) < 2:
            self.notify("usage: tell <node> <message>", severity="error")
            return
        target = args[0]
        message = " ".join(args[1:])
        sender = "you"
        if self._current_node_id is not None and self._current_node_id != target:
            sender = self._current_node_id
        self._inject_message_to_node(
            target_node_id=target, content=message, sender=sender,
        )

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
        configured resume_args (e.g. `claude --continue`). Auto-resume
        on app launch usually makes this redundant; it's still handy
        if you spawned an agent and want to pick up an older chat in
        the same worktree.
        """
        await self._respawn_node(args, resume=True)

    async def _cmd_fresh(self, args: list[str]) -> None:
        """Restart a node from scratch — the inverse of :resume.

        Tears down the current pane and respawns without resume_args,
        so the agent forgets its prior conversation. Useful when
        auto-resume picked up history you no longer want.
        """
        await self._respawn_node(args, resume=False)

    async def _respawn_node(self, args: list[str], *, resume: bool) -> None:
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
            verb = "resume" if resume else "fresh"
            self.notify(
                f"{verb} is only for persistent nodes", severity="warning",
            )
            return
        recipe = self.config.agent_for_node(node.spec)
        if resume and not recipe.resume_args:
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
        await self._mount_panel_for(node, switcher, resume=resume)
        node.status = "idle"
        self._focus_node(node.spec.id)
        self.notify(
            f"{'resumed' if resume else 'restarted fresh'} {node.spec.id}"
        )
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

    async def _cmd_images(self, _args: list[str]) -> None:
        """Show captured-image count + path to the session's image dir."""
        if self._session is None:
            return
        img_dir = self._session.image_dir
        if not img_dir.exists():
            self.notify("no images captured yet", timeout=4)
            return
        files = sorted(
            (p for p in img_dir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            self.notify("no images captured yet", timeout=4)
            return
        latest = files[0]
        self.notify(
            f"{len(files)} image(s) in {img_dir}; latest: {latest.name}",
            timeout=8,
        )

    async def _cmd_open_image(self, args: list[str]) -> None:
        """Open the most recent captured image (or a specific one by name)
        in the system default viewer.
        """
        if self._session is None:
            return
        img_dir = self._session.image_dir
        target: Path | None = None
        if args:
            cand = img_dir / args[0]
            if not cand.exists():
                self.notify(f"no such image: {args[0]}", severity="warning")
                return
            target = cand
        else:
            if img_dir.exists():
                files = sorted(
                    (p for p in img_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                target = files[0] if files else None
        if target is None:
            self.notify("no images captured yet", timeout=4)
            return
        # Use the platform-appropriate opener. macOS: open; Linux:
        # xdg-open; otherwise fall through and just print the path.
        opener = None
        if sys.platform == "darwin":
            opener = "open"
        elif sys.platform.startswith("linux"):
            opener = "xdg-open"
        if opener is None:
            self.notify(f"open externally: {target}", timeout=8)
            return
        try:
            subprocess.Popen([opener, str(target)], start_new_session=True)
            self.notify(f"opened {target.name}", timeout=4)
        except (OSError, FileNotFoundError) as e:
            self.notify(f"opener failed ({e}); path: {target}", severity="warning")


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
