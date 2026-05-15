"""End-to-end smoke test: init a workspace, build a pipeline, hand off.

Validates the new plumbing without launching the TUI:
  - `Workspace.init` scaffolds .term/ in an empty repo
  - `load_config` merges bundled defaults
  - `PipelineRun.initialize` creates one worktree per node
  - `handoff` snapshots files across worktrees and they accumulate as
    artifacts flow from node 0 → node 1 → node 2
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from term.config import load_config
from term.handoff import handoff
from term.pipeline import PipelineRun
from term.workspace import Workspace


def _run(*args, cwd) -> str:
    cp = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)
    return cp.stdout


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="term-smoke-"))
    try:
        print(f"workspace: {tmp}")

        ws = Workspace.init(tmp)
        assert ws.term_dir.is_dir()
        assert (ws.term_dir / ".gitignore").read_text().strip() == "worktrees/"
        print("  init OK")

        # Bundled defaults now start with an empty pipeline; write a starter
        # spec-review-build pipeline for this test.
        (ws.term_dir / "pipeline.toml").write_text("""
[pipeline]
name = "spec-review-build"
nodes = [
  { role = "spec-writer", mode = "persistent" },
  { role = "reviewer",    mode = "persistent" },
  { role = "builder",     mode = "persistent" },
]
""")

        cfg = load_config(ws.root)
        assert "claude-code" in cfg.agents
        assert "codex" in cfg.agents
        assert len(cfg.pipeline.nodes) == 3
        node_ids = [n.id for n in cfg.pipeline.nodes]
        # Verify backward-compat agent derivation from role.
        agents = [n.agent for n in cfg.pipeline.nodes]
        assert agents == ["claude-code", "codex", "claude-code"], agents
        print(f"  loaded pipeline nodes: {node_ids}")
        print(f"  agents derived from roles: {agents}")

        run = PipelineRun(cfg, ws)
        run.initialize()
        assert len(run.nodes) == 3
        for n in run.nodes:
            assert n.worktree.path.is_dir()
            assert (n.worktree.path / ".git").exists(), f"{n.worktree.path} missing .git"
        print(f"  worktrees created: {[str(n.worktree.path.relative_to(ws.root)) for n in run.nodes]}")

        spec, review, build = run.nodes

        # Stage spec.md in the spec-writer worktree.
        (spec.worktree.path / "SPEC.md").write_text("# Spec\nDo the thing.\n")
        r1 = handoff(ws, spec, review)
        assert r1.ok, r1.message
        assert "SPEC.md" in r1.files_changed, r1.files_changed
        assert (review.worktree.path / "SPEC.md").read_text().startswith("# Spec")
        print(f"  spec → review: {r1.message}")

        # Reviewer adds REVIEW.md. Handoff to builder should carry BOTH files.
        (review.worktree.path / "REVIEW.md").write_text("# Review\nLGTM with nits.\n")
        r2 = handoff(ws, review, build)
        assert r2.ok, r2.message
        assert (build.worktree.path / "SPEC.md").exists(), "SPEC.md should accumulate"
        assert (build.worktree.path / "REVIEW.md").exists(), "REVIEW.md should arrive"
        print(f"  review → build: {r2.message}")
        print(f"     builder worktree has: {sorted(f.name for f in build.worktree.path.iterdir() if not f.name.startswith('.'))}")

        # Re-handoff with no new work: target's tree already matches source,
        # so tar-extract leaves the worktree clean — succeeds with a "no
        # changes vs target" message rather than committing an empty handoff.
        r3 = handoff(ws, review, build)
        assert r3.ok and "no changes" in r3.message, r3
        print(f"  no-op handoff: {r3.message}")

        print("OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
