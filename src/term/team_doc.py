"""Per-worktree team-roster section, merged into AGENTS.md.

`AGENTS.md` is a popular convention for project operational docs (Claude
Code and Codex both auto-read it). Many repos already have one. Term
writes ONLY between marker comments so existing content is preserved:

    ... your project's AGENTS.md content ...

    <!-- term:team-roster:start -->
    (term-managed block: refreshed automatically)
    <!-- term:team-roster:end -->

If AGENTS.md doesn't exist yet, we create it with just the managed block
(prefixed by a short heading) so we never silently introduce surprising
content into a tracked file.
"""

from __future__ import annotations

from pathlib import Path

from term.pipeline import NodeState


MARKER_START = "<!-- term:team-roster:start -->"
MARKER_END = "<!-- term:team-roster:end -->"


_BLOCK = """\
## Other agents in this term session

You are **{my_display}** (id: `{my_id}`).
Working in: `{my_cwd}`

{team_block}

### How to talk to them

This terminal is wrapped by **term**, a multi-agent TUI. Any time you
want to message another agent, **write a text file** at:

    ../../mail/{my_id}__to__<their-id>.txt

with the body of your message. Example:

    echo "hey alice, can you review SPEC.md?" \\
      > ../../mail/{my_id}__to__alice.txt

Term picks the file up within ~1.5s, delivers it to their terminal as
`[From {my_id}]: <message>`, and deletes the file.

Your inbox arrives the same way — anything addressed to you appears as
a paste at your prompt prefixed `[From <sender>]:`. Reply by writing
back to their mailbox.

You may coordinate autonomously without asking the user first. If you
need information from a peer, message them. If you have something they
should know, tell them. The mailbox is asynchronous; replies appear as
inbound pastes whenever they arrive.
"""


def render_team_block(self_node: NodeState, others: list[NodeState]) -> str:
    if not others:
        team_block = "_No other agents in this session yet._"
    else:
        lines = []
        for n in others:
            name = n.spec.display
            role = n.spec.role or "(no role)"
            lines.append(
                f"- **{name}** — id: `{n.spec.id}`, agent: `{n.spec.agent}`, "
                f"role: `{role}`, mode: {n.spec.mode}"
            )
        team_block = "\n".join(lines)
    return _BLOCK.format(
        my_display=self_node.spec.display,
        my_id=self_node.spec.id,
        my_cwd=self_node.worktree.path,
        team_block=team_block,
    )


def _wrap(block: str) -> str:
    return f"{MARKER_START}\n{block.rstrip()}\n{MARKER_END}"


def _merge_into_existing(existing: str, block: str) -> str:
    """Replace the managed region in `existing`, or append it if absent."""
    wrapped = _wrap(block)
    if MARKER_START in existing and MARKER_END in existing:
        start = existing.index(MARKER_START)
        end_marker_pos = existing.index(MARKER_END, start) + len(MARKER_END)
        return existing[:start] + wrapped + existing[end_marker_pos:]
    # Existing AGENTS.md with no managed region → append at the bottom.
    sep = "\n\n" if not existing.endswith("\n") else "\n"
    return existing.rstrip() + sep + "\n" + wrapped + "\n"


def write_team_docs(nodes: list[NodeState]) -> None:
    """Refresh the team-roster region of AGENTS.md in every worktree.

    Preserves any existing AGENTS.md content; only touches the region
    between MARKER_START and MARKER_END. Creates the file if missing.
    """
    for me in nodes:
        others = [n for n in nodes if n.spec.id != me.spec.id]
        block = render_team_block(me, others)
        wrapped = _wrap(block)
        path = me.worktree.path / "AGENTS.md"
        try:
            if path.exists():
                existing = path.read_text()
                updated = _merge_into_existing(existing, block)
                if updated != existing:
                    path.write_text(updated)
            else:
                # New file: include a small header so it's clearly project doc.
                path.write_text(
                    "# AGENTS.md\n\n"
                    "_This file is auto-managed in part by term._\n\n"
                    f"{wrapped}\n"
                )
        except OSError:
            pass
