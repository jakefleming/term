"""Multi-session persistence.

A workspace can hold N sessions. Each session is an independent pipeline
with its own worktrees and branches. Switching sessions tears down the
current panes and loads the other's; conversations resume via `:resume`.

Layout:

    .term/
      pipeline.toml                 # seed for new sessions (unchanged)
      sessions/
        index.json                  # SessionsIndex: list + current_id
        <session-id>/
          session.json              # this session's pipeline state
          worktrees/<node-id>/      # per-node git worktrees

Pre-multi-session layouts (a top-level `.term/session.json` plus
`.term/worktrees/`) auto-migrate into a session called "default" on first
launch. The default session keeps the legacy paths/branches in place so
no worktrees have to move.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from term.config import NodeSpec
from term.pipeline import NodeState, PipelineRun
from term.workspace import Workspace, git


_VERSION = 2


@dataclass
class SessionInfo:
    """Metadata for one session within a workspace."""
    id: str
    name: str
    worktree_root: str    # relative to workspace.root
    branch_prefix: str    # e.g. "term/<id>/node"
    created_at: str = ""


@dataclass
class SavedNode:
    id: str
    agent: str
    role: str | None
    mode: str
    seen: bool = False
    last_status: str = "idle"
    display_name: str | None = None


def _slugify(name: str) -> str:
    """Internal: slugify session names. See `slugify` for node names."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "session"


def slugify(name: str) -> str:
    """Turn a free-form name into a filesystem/branch-safe slug."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "node"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Session:
    """Per-session state: a list of nodes, persisted to JSON."""

    def __init__(self, workspace: Workspace, info: SessionInfo) -> None:
        self.workspace = workspace
        self.info = info
        self._dir = workspace.term_dir / "sessions" / info.id
        self.worktree_root = workspace.root / info.worktree_root
        self.path = self._dir / "session.json"
        # Mailbox: agents drop `to-<node-id>.txt` files here to send a
        # message to a peer in this session. Term watches the directory
        # and delivers them as bracketed-paste into the target pane.
        self.mail_dir = self._dir / "mail"
        # Spawn queue: agents drop TOML files here to request a one-shot
        # sub-task (different model, specialist role, parallel work).
        # Term picks them up, runs the sub, delivers stdout back via the
        # mailbox.
        self.spawn_dir = self._dir / "spawn"

    def ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.mail_dir.mkdir(parents=True, exist_ok=True)
        self.spawn_dir.mkdir(parents=True, exist_ok=True)

    def drain_mail(self) -> list[tuple[str, str, str | None]]:
        """Read and consume any pending mail.

        Filename conventions accepted:
            to-<target>.txt
            to-<target>.<timestamp>.txt
            <sender>__to__<target>.txt

        Returns a list of (target_id, content, sender_id_or_None) in mtime
        order. Files are deleted after read.
        """
        if not self.mail_dir.exists():
            return []
        results: list[tuple[str, str, str | None, float]] = []
        for p in self.mail_dir.iterdir():
            if not p.is_file():
                continue
            stem = p.stem
            target: str | None = None
            sender: str | None = None
            if "__to__" in stem:
                left, right = stem.split("__to__", 1)
                sender = left or None
                target = right.split(".", 1)[0] or None
            elif stem.startswith("to-"):
                target = stem[3:].split(".", 1)[0] or None
            if not target:
                continue
            try:
                content = p.read_text(errors="replace").strip()
                mtime = p.stat().st_mtime
                p.unlink()
            except OSError:
                continue
            results.append((target, content, sender, mtime))
        results.sort(key=lambda t: t[3])
        return [(t, c, s) for (t, c, s, _) in results]

    def drain_spawn_requests(self) -> list[tuple[str, dict, Path]]:
        """Pull pending spawn requests from `spawn_dir`.

        Returns a list of `(sender_id, payload, source_path)` tuples in
        mtime order. Caller is responsible for deleting the source file
        once it has decided to run / reject the request (so a failed run
        doesn't lose the request).

        Filename: `<sender>__spawn__<id>.toml` (matches the mailbox
        `<sender>__to__<target>.txt` convention).
        """
        if not self.spawn_dir.exists():
            return []
        import tomllib
        out: list[tuple[str, dict, Path, float]] = []
        for p in self.spawn_dir.iterdir():
            if not p.is_file() or p.suffix != ".toml":
                continue
            stem = p.stem
            sender = ""
            if "__spawn__" in stem:
                sender = stem.split("__spawn__", 1)[0]
            try:
                payload = tomllib.loads(p.read_text())
                mtime = p.stat().st_mtime
            except (OSError, ValueError) as e:
                # Malformed: rename out of the way so we don't reprocess.
                try:
                    p.rename(p.with_suffix(p.suffix + f".error-{int(time.time())}"))
                except OSError:
                    pass
                payload = {"_error": f"parse failed: {e}"}
                mtime = 0.0
            out.append((sender, payload, p, mtime))
        out.sort(key=lambda t: t[3])
        return [(s, pl, pa) for (s, pl, pa, _) in out]

    def branch_for(self, node_id: str) -> str:
        return f"{self.info.branch_prefix}/{node_id}"

    def path_for(self, node_id: str) -> Path:
        return self.worktree_root / node_id

    def load(self) -> list[SavedNode] | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        result: list[SavedNode] = []
        for n in raw.get("nodes", []):
            try:
                result.append(SavedNode(
                    id=n["id"],
                    agent=n["agent"],
                    role=n.get("role"),
                    mode=n.get("mode", "persistent"),
                    seen=bool(n.get("seen", False)),
                    last_status=n.get("last_status", "idle"),
                    display_name=n.get("display_name"),
                ))
            except KeyError:
                continue
        return result

    def save(self, pipeline: PipelineRun, current_node_id: str | None) -> None:
        self.ensure()
        payload = {
            "version": _VERSION,
            "current_node_id": current_node_id,
            "nodes": [
                {
                    "id": n.spec.id,
                    "agent": n.spec.agent,
                    "role": n.spec.role,
                    "mode": n.spec.mode,
                    "seen": n.seen,
                    "last_status": n.status,
                    "display_name": n.spec.display_name,
                }
                for n in pipeline.nodes
            ],
        }
        try:
            self.path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass


class SessionManager:
    """Owns the workspace-wide sessions index (.term/sessions/index.json).

    Responsibilities: list / create / switch / delete sessions, plus a
    one-time migration from the pre-multi-session layout.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.sessions_dir = workspace.term_dir / "sessions"
        self.index_path = self.sessions_dir / "index.json"
        self._index: dict | None = None

    def _load_index(self) -> dict:
        if self._index is not None:
            return self._index
        if self.index_path.exists():
            try:
                self._index = json.loads(self.index_path.read_text())
                if "sessions" not in self._index:
                    self._index["sessions"] = []
                return self._index
            except (OSError, json.JSONDecodeError):
                pass
        self._index = {"current": None, "sessions": []}
        return self._index

    def _save_index(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self._index, indent=2))

    def migrate_legacy(self) -> None:
        """Fold a pre-multi-session layout into a 'default' session."""
        idx = self._load_index()
        if idx["sessions"]:
            return
        old_session = self.workspace.term_dir / "session.json"
        old_worktrees = self.workspace.term_dir / "worktrees"
        has_legacy = old_session.exists() or (
            old_worktrees.exists() and any(old_worktrees.iterdir())
        )
        if not has_legacy:
            return
        info = SessionInfo(
            id="default",
            name="default",
            worktree_root=".term/worktrees",  # legacy path; no move needed
            branch_prefix="term/node",         # legacy branch prefix
            created_at=_now(),
        )
        session_dir = self.sessions_dir / info.id
        session_dir.mkdir(parents=True, exist_ok=True)
        if old_session.exists():
            shutil.move(str(old_session), str(session_dir / "session.json"))
        idx["sessions"].append(info.__dict__)
        idx["current"] = info.id
        self._save_index()

    def list(self) -> list[SessionInfo]:
        return [SessionInfo(**s) for s in self._load_index()["sessions"]]

    def current_id(self) -> str | None:
        return self._load_index().get("current")

    def get(self, session_id: str) -> SessionInfo | None:
        for s in self.list():
            if s.id == session_id:
                return s
        return None

    def get_or_create_default(self) -> SessionInfo:
        """Return the currently-active session, or create one if none."""
        idx = self._load_index()
        if idx["sessions"]:
            cur = idx.get("current") or idx["sessions"][0]["id"]
            for s in idx["sessions"]:
                if s["id"] == cur:
                    return SessionInfo(**s)
            return SessionInfo(**idx["sessions"][0])
        return self.create("default", as_current=True)

    def create(self, name: str, *, as_current: bool = True) -> SessionInfo:
        idx = self._load_index()
        existing_ids = {s["id"] for s in idx["sessions"]}
        base = _slugify(name)
        sid = base
        i = 2
        while sid in existing_ids:
            sid = f"{base}-{i}"
            i += 1
        info = SessionInfo(
            id=sid,
            name=name,
            worktree_root=f".term/sessions/{sid}/worktrees",
            branch_prefix=f"term/{sid}/node",
            created_at=_now(),
        )
        idx["sessions"].append(info.__dict__)
        if as_current or not idx["current"]:
            idx["current"] = sid
        self._save_index()
        (self.sessions_dir / sid).mkdir(parents=True, exist_ok=True)
        return info

    def set_current(self, session_id: str) -> None:
        idx = self._load_index()
        if not any(s["id"] == session_id for s in idx["sessions"]):
            raise KeyError(session_id)
        idx["current"] = session_id
        self._save_index()

    def delete(self, session_id: str) -> None:
        info = self.get(session_id)
        if info is None:
            return
        # Remove worktrees on disk (force; may have uncommitted changes).
        root = self.workspace.root / info.worktree_root
        if root.exists():
            for child in root.iterdir():
                if (child / ".git").exists():
                    git(
                        ["worktree", "remove", "--force", str(child)],
                        cwd=self.workspace.root, check=False,
                    )
        # Remove the session metadata dir.
        sdir = self.sessions_dir / session_id
        if sdir.exists():
            shutil.rmtree(sdir, ignore_errors=True)
        # Update index.
        idx = self._load_index()
        idx["sessions"] = [s for s in idx["sessions"] if s["id"] != session_id]
        if idx["current"] == session_id:
            idx["current"] = idx["sessions"][0]["id"] if idx["sessions"] else None
        self._save_index()

    def rename(self, session_id: str, new_name: str) -> None:
        idx = self._load_index()
        for s in idx["sessions"]:
            if s["id"] == session_id:
                s["name"] = new_name
                break
        self._save_index()

    def open_session(self, info: SessionInfo) -> Session:
        return Session(self.workspace, info)


def seed_pipeline_from_config(
    pipeline: PipelineRun,
    session: Session,
) -> None:
    """Populate `pipeline.nodes` from the declared pipeline.toml nodes,
    creating worktrees under the session's layout. Used when a session is
    brand new and there's no saved state to restore.
    """
    for spec in pipeline.config.pipeline.nodes:
        wt = pipeline.workspace.ensure_worktree_at(
            session.path_for(spec.id),
            session.branch_for(spec.id),
            node_id=spec.id,
        )
        pipeline.nodes.append(NodeState(spec=spec, worktree=wt))


def restore_pipeline(
    pipeline: PipelineRun,
    session: Session,
    saved: list[SavedNode],
    config_role_names: set[str],
    config_agent_names: set[str],
) -> tuple[int, int]:
    """Populate `pipeline.nodes` from a session's saved entries.

    Returns (restored_count, skipped_count). Entries whose agent or role
    no longer exists in the config are skipped so the caller can notify.
    """
    restored = 0
    skipped = 0
    for sn in saved:
        if sn.agent not in config_agent_names:
            skipped += 1
            continue
        if sn.role is not None and sn.role not in config_role_names:
            skipped += 1
            continue
        spec = NodeSpec(
            id=sn.id, agent=sn.agent, role=sn.role, mode=sn.mode,
            display_name=sn.display_name,
        )
        wt = pipeline.workspace.ensure_worktree_at(
            session.path_for(sn.id),
            session.branch_for(sn.id),
            node_id=sn.id,
        )
        state = NodeState(spec=spec, worktree=wt)
        state.seen = sn.seen
        state.status = sn.last_status if sn.last_status in {
            "idle", "running", "ready", "blocked", "exited"
        } else "idle"
        pipeline.nodes.append(state)
        restored += 1
    return restored, skipped
