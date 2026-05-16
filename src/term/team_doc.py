"""Per-worktree team-roster doc.

Term writes (and keeps updated) an `AGENTS.md` in every node's worktree
listing the other agents in the session and how to talk to them through
the mailbox. Claude Code and Codex both auto-read AGENTS.md in their
cwd, so this is how the agents discover their peers and the messaging
convention without you having to brief them by hand.

Regenerated whenever the roster changes (spawn / delete / swap / rename
/ session switch).
"""

from __future__ import annotations

from pathlib import Path

from term.pipeline import NodeState


_TEMPLATE = """\
# Your team in this term session

You are **{my_display}** (id: `{my_id}`).
Working in the worktree at: `{my_cwd}`

## Other agents in this session

{team_block}

## How to talk to them

This terminal is wrapped by **term**, a TUI multi-agent orchestrator. Any
time you want to message another agent, write a text file at:

    ../../mail/{my_id}__to__<their-id>.txt

with the body of your message. Example:

    echo "hi alice, can you take a look at SPEC.md?" \\
      > ../../mail/{my_id}__to__alice.txt

term picks the file up within ~1.5 seconds, delivers the contents into
their terminal as `[From {my_id}]: <your message>`, and deletes the file.

Your inbox arrives the same way — anything addressed to you appears as
a paste at your prompt prefixed `[From <sender>]:`. Reply by writing
back to their mailbox.

You may coordinate autonomously without asking the user first. If you
need information from a peer, ask them. If you have something they
should know, tell them. The mailbox is asynchronous; check your prompt
periodically for replies (they appear as inbound paste events).

If you're unsure who does what, the team list above shows each agent's
agent (CLI), role (prompt context), and id.
"""


def render_agents_md(self_node: NodeState, others: list[NodeState]) -> str:
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
    return _TEMPLATE.format(
        my_display=self_node.spec.display,
        my_id=self_node.spec.id,
        my_cwd=self_node.worktree.path,
        team_block=team_block,
    )


def write_team_docs(nodes: list[NodeState]) -> None:
    """Refresh AGENTS.md in every node's worktree."""
    for me in nodes:
        others = [n for n in nodes if n.spec.id != me.spec.id]
        path = me.worktree.path / "AGENTS.md"
        try:
            path.write_text(render_agents_md(me, others))
        except OSError:
            pass
