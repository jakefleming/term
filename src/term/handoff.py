"""Handoff: deliver a snapshot of source's worktree into target's worktree.

Design note (deviation from SPEC.md):

  SPEC.md proposed `git cherry-pick` as the handoff primitive. In practice
  cherry-pick only carries the diff of the most recent commit, which breaks
  third-node-and-beyond pipelines — e.g. the builder would receive REVIEW.md
  but not the original SPEC.md unless we explicitly chain picks.

  We snapshot the source's full tracked-file state instead, using
  `git archive HEAD | tar -xf - -C <target>`. The artifact is the full
  worktree, not the delta. Simpler, more robust, naturally cumulative.

  SPEC.md should be amended next pass to reflect this; flagging here so the
  drift is visible.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from term.pipeline import NodeState
from term.workspace import GitError, Workspace, git


@dataclass
class HandoffResult:
    ok: bool
    source_id: str
    target_id: str
    commit: str | None
    message: str
    files_changed: list[str] = field(default_factory=list)


def handoff(
    workspace: Workspace,
    source: NodeState,
    target: NodeState,
) -> HandoffResult:
    """Snapshot source's tracked files into target and commit there."""
    src = source.worktree.path
    tgt = target.worktree.path

    # 1. Auto-commit source if dirty so the snapshot includes pending work.
    if workspace.is_dirty(src):
        git(["add", "-A"], cwd=src)
        try:
            git(
                ["commit", "-q", "-m", f"handoff: {source.spec.id}"],
                cwd=src,
            )
        except GitError as e:
            # `git commit` returns 1 if nothing actually staged (e.g. only
            # ignored files dirtied). Treat as not-an-error.
            if "nothing to commit" not in (e.stdout + e.stderr).lower():
                raise

    src_head = workspace.head(src)
    if source.last_handoff_commit == src_head:
        return HandoffResult(
            ok=False,
            source_id=source.spec.id,
            target_id=target.spec.id,
            commit=src_head,
            message="nothing new to hand off",
        )

    # 2. Auto-commit target so its work is preserved before we overlay source.
    if workspace.is_dirty(tgt):
        git(["add", "-A"], cwd=tgt)
        try:
            git(
                ["commit", "-q", "-m", f"pre-handoff: {target.spec.id} work"],
                cwd=tgt,
            )
        except GitError:
            pass  # see note above

    # 3. Snapshot source HEAD's tracked tree into target via tar over pipe.
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=str(src), capture_output=True, check=True,
    )
    extract = subprocess.run(
        ["tar", "-xf", "-"],
        cwd=str(tgt), input=archive.stdout, capture_output=True,
    )
    if extract.returncode != 0:
        return HandoffResult(
            ok=False,
            source_id=source.spec.id,
            target_id=target.spec.id,
            commit=src_head,
            message=f"tar extract failed: {extract.stderr.decode(errors='replace').strip()}",
        )

    # 4. Diff & commit the new state in target.
    if not workspace.is_dirty(tgt):
        source.last_handoff_commit = src_head
        return HandoffResult(
            ok=True,
            source_id=source.spec.id,
            target_id=target.spec.id,
            commit=src_head,
            message="handoff applied (no changes vs target)",
        )

    files = _changed_files(tgt)
    git(["add", "-A"], cwd=tgt)
    git(
        [
            "commit",
            "-q",
            "-m",
            f"handoff from {source.spec.id} ({src_head[:8]})",
        ],
        cwd=tgt,
    )
    source.last_handoff_commit = src_head
    return HandoffResult(
        ok=True,
        source_id=source.spec.id,
        target_id=target.spec.id,
        commit=src_head,
        message=f"handed off {src_head[:8]} → {target.spec.id} ({len(files)} files)",
        files_changed=files,
    )


def _changed_files(worktree: Path) -> list[str]:
    cp = git(["status", "--porcelain"], cwd=worktree)
    out = cp.stdout.decode(errors="replace")
    return [line[3:] for line in out.splitlines() if line.strip()]
