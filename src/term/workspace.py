"""Git worktree management for term.

A `Workspace` wraps a git repo root and maintains a `.term/worktrees/<node-id>/`
directory per node, each on its own `term/node/<node-id>` branch. We shell out
to `git` directly — no pygit2 / GitPython — for legibility of failures.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Worktree:
    node_id: str
    path: Path
    branch: str


class GitError(RuntimeError):
    """A `git` command exited non-zero. Carries stderr for surfacing."""

    def __init__(self, args: list[str], rc: int, stdout: str, stderr: str) -> None:
        super().__init__(
            f"git {' '.join(args)} exited {rc}\n--- stderr ---\n{stderr.strip()}"
        )
        self.args = args
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr


# term's own internal commits (workspace init, handoff snapshots, etc.) are
# tool plumbing, not author-attributed work. We disable GPG signing on those
# so e.g. a user with `commit.gpgsign=true` globally doesn't have every
# handoff commit get signed-as-them.
_NO_SIGN = ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def git(
    args: list[str],
    *,
    cwd: Path | str,
    check: bool = True,
    input_bytes: bytes | None = None,
    binary_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run `git <args>` with friendlier failure messages.

    For `commit` invocations we inject -c flags that disable GPG signing so
    term's internal plumbing commits don't get author-signed.
    """
    full = ["git"]
    if args and args[0] == "commit":
        full += _NO_SIGN
    full += args
    cp = subprocess.run(
        full,
        cwd=str(cwd),
        input=input_bytes,
        capture_output=True,
    )
    if check and cp.returncode != 0:
        raise GitError(
            args,
            cp.returncode,
            cp.stdout.decode(errors="replace"),
            cp.stderr.decode(errors="replace"),
        )
    if not binary_output:
        cp.stdout_text = cp.stdout.decode(errors="replace")  # type: ignore[attr-defined]
        cp.stderr_text = cp.stderr.decode(errors="replace")  # type: ignore[attr-defined]
    return cp


class Workspace:
    """A git repo plus a `.term/` directory holding per-node worktrees."""

    BRANCH_PREFIX = "term/node/"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.term_dir = self.root / ".term"
        self.worktrees_dir = self.term_dir / "worktrees"

    @classmethod
    def discover(cls, start: Path) -> "Workspace":
        """Walk up from `start` to find the git repo root."""
        cp = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start), capture_output=True, text=True,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"not inside a git repository (cwd={start}). "
                f"Run `term init` to set one up."
            )
        return cls(Path(cp.stdout.strip()))

    @classmethod
    def init(cls, root: Path) -> "Workspace":
        """Make `root` into a term workspace. Idempotent."""
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)

        ws = cls(root)
        ws.term_dir.mkdir(exist_ok=True)
        ws.worktrees_dir.mkdir(exist_ok=True)

        gi = ws.term_dir / ".gitignore"
        if not gi.exists():
            gi.write_text("worktrees/\n")

        # Repo must have at least one commit so worktrees can branch off it.
        has_head = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=str(root), capture_output=True,
        ).returncode == 0
        if not has_head:
            (ws.term_dir / "README").write_text(
                "term workspace metadata. Worktrees live under worktrees/.\n"
            )
            git(["add", ".term/.gitignore", ".term/README"], cwd=root)
            git(["commit", "-q", "-m", "term: initialize workspace"], cwd=root)

        return ws

    def branch_for(self, node_id: str) -> str:
        return f"{self.BRANCH_PREFIX}{node_id}"

    def path_for(self, node_id: str) -> Path:
        return self.worktrees_dir / node_id

    def prepare(self) -> None:
        """Idempotent: ensure .term/ scaffolding exists. No git side effects."""
        self.term_dir.mkdir(exist_ok=True)
        self.worktrees_dir.mkdir(exist_ok=True)
        gi = self.term_dir / ".gitignore"
        if not gi.exists():
            gi.write_text("worktrees/\n")

    def ensure_worktree(self, node_id: str, base: str = "HEAD") -> Worktree:
        """Create the worktree if missing; return its handle either way."""
        path = self.path_for(node_id)
        branch = self.branch_for(node_id)
        if path.exists() and (path / ".git").exists():
            return Worktree(node_id=node_id, path=path, branch=branch)

        self.prepare()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not (path / ".git").exists():
            # stale dir from prior abandoned setup
            shutil.rmtree(path)

        branch_exists = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", branch],
            cwd=str(self.root), capture_output=True,
        ).returncode == 0

        if branch_exists:
            git(["worktree", "add", str(path), branch], cwd=self.root)
        else:
            git(["worktree", "add", "-b", branch, str(path), base], cwd=self.root)

        return Worktree(node_id=node_id, path=path, branch=branch)

    def remove_worktree(self, node_id: str, force: bool = False) -> None:
        path = self.path_for(node_id)
        if not path.exists():
            return
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        git(args, cwd=self.root, check=False)

    def is_dirty(self, worktree_path: Path) -> bool:
        cp = git(["status", "--porcelain"], cwd=worktree_path)
        return bool(cp.stdout.strip())

    def head(self, worktree_path: Path) -> str:
        cp = git(["rev-parse", "HEAD"], cwd=worktree_path)
        return cp.stdout.decode().strip()
