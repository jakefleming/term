# term — spec v0.1

A TUI that orchestrates multiple CLI coding agents (Claude Code, Codex, Gemini,
Aider, …) as a pipeline. The human is the floor manager: artifacts move from
one agent to the next, the human can edit them in flight, reroute, or jump
into any agent's live shell to nudge it.

Auth is whatever the user already has on their machine — we spawn the real
CLIs, so subscription accounts Just Work. No API keys to manage.

## Problem

Today, running a multi-agent workflow across CLI agents looks like: open three
terminals, copy-paste between them, lose track of which version of the spec
the reviewer saw, forget to commit. We want one screen where the workflow is
visible, artifacts are addressable, and handoffs are a keystroke.

## Non-goals

- Agents talking *autonomously* to each other in a loop. Human is always in
  the loop on each handoff (an auto-relay toggle may come later).
- Replacing tmux. We're not building a general-purpose multiplexer.
- API/SDK integration. We strictly drive the real CLIs.
- Hosting / multi-user. Single user, local machine.

## Concepts

**Workspace** — a git repo. The substrate everything else lives in. Either
the user's existing repo or a scratch one created by `term init`.

**Worktree** — each agent runs in its own `git worktree` off the workspace,
on its own branch. This gives every agent a real isolated working directory
that shares history with the others. No two agents ever step on each other's
files.

**Agent** — a CLI we know how to launch. Defined in config as a recipe:
command, args, env, plus an optional one-shot invocation form. Example: a
`claude` recipe knows that interactive mode is `claude` and one-shot is
`claude -p "{prompt}"`.

**Role** — an agent + a prompt template + (optionally) a default model
selection. "Spec writer," "reviewer," "builder" are roles. Roles wrap
incoming artifacts in role-appropriate framing so you don't retype it.

**Node** — an instance of a role in a pipeline. A pipeline can have two
"reviewer" nodes if it wants; each is its own worktree and pane.

**Pipeline** — an ordered (v1: linear) sequence of nodes. Edges between
nodes carry artifacts. The pipeline definition is a small TOML file; the
running pipeline is the live state machine.

**Artifact** — a git commit (or set of changed files) produced by one node
and consumed by the next. *The filesystem is the bus.* Not stdout text.

**Handoff** — the transition where node A's artifact becomes node B's
input. Mechanically: snapshot A's worktree (commit anything uncommitted),
deliver the change into B's worktree, prompt B's agent with the
role-templated instructions.

## Architecture

```
┌─ Workspace (git repo) ─────────────────────────────────┐
│                                                        │
│  main ─────────────────────────────────────────────►   │
│       │                                                │
│       ├── worktree: nodes/spec-writer    (branch A)    │
│       ├── worktree: nodes/reviewer       (branch B)    │
│       └── worktree: nodes/builder        (branch C)    │
│                                                        │
└────────────────────────────────────────────────────────┘
         ▲             ▲             ▲
         │             │             │
    PTY pane     PTY pane      PTY pane
   (claude)     (codex)       (claude)
         │             │             │
         └──── term TUI orchestrator ┘
              pipeline view · artifact tray · diff viewer
```

Each pane is a real PTY running the agent's CLI with `cwd` set to that
node's worktree. The TUI watches process state, tracks worktree git status,
and brokers handoffs.

### Handoff mechanics (v1)

When the user triggers handoff `A → B`:

1. **Snapshot A.** In A's worktree: `git add -A && git commit -m "handoff: <node-A>"` if dirty.
   Record commit SHA.
2. **(Optional) Edit the artifact.** User can open the diff or specific
   files in `$EDITOR` before forwarding. This is the "interject" point.
3. **Deliver to B.** In B's worktree, apply A's changes. v1 default:
   `git cherry-pick <A-sha>` onto B's branch. Conflicts pause the pipeline
   with a clear UI; user resolves in B's worktree, then continues.
4. **Prompt B.** Send the role-templated prompt into B's pane (or, for
   one-shot nodes, run `agent -p "<prompt>"`). The template can reference
   `{artifact.files}`, `{artifact.diff}`, `{artifact.commit}`.

### Persistent vs one-shot nodes

A node can be one of:

- **Persistent.** Long-running interactive CLI in a PTY pane. User can
  watch, interject by focusing the pane and typing, or wait for it to
  settle. The TUI considers the node "ready to hand off" when the user
  says so (explicit `:handoff` action). No prompt-detection heuristics in v1.
- **One-shot.** No pane. Triggering the node runs
  `agent -p "<wrapped-prompt>"` in the worktree, captures the file changes
  the agent makes (= the artifact), and surfaces them in the diff viewer.
  Faster, less to watch, good for "run the linter agent" style steps.

The node type is part of the role definition but overridable per-pipeline.

### Interjection model

Three flavors, all useful:

- **Mid-task chat.** Just focus the pane and type. Free because the pane
  is a live CLI.
- **Edit artifact in flight.** Pre-handoff, open the diff/files in
  `$EDITOR` (or an inline buffer), save, continue. Your edits ride along
  with the agent's into the next node.
- **Reroute.** Instead of `A → B`, send A's artifact to a different node
  (existing or newly spawned). Pipeline graph reflects the change.

## UX

### Layout

```
┌─ pipeline ──┬──────────────────────────────────────┬─ artifact ─┐
│             │                                      │            │
│ ● spec      │   active pane (PTY of focused node)  │  diff      │
│ │           │                                      │  viewer    │
│ ● review    │   [claude code running here]         │            │
│ │           │                                      │  files:    │
│ ○ build     │                                      │  + SPEC.md │
│             │                                      │            │
├─────────────┴──────────────────────────────────────┴────────────┤
│ : command palette        F2 handoff   F3 edit   F4 reroute      │
└─────────────────────────────────────────────────────────────────┘
```

- **Pipeline sidebar (left).** Nodes top-to-bottom. Each shows role,
  agent, branch, status (idle / running / ready-to-hand-off / blocked).
  ↑/↓ moves focus, Enter swaps the center pane.
- **Active pane (center).** The PTY of the focused node. Acts like a
  normal terminal — type, scroll, etc.
- **Artifact tray (right).** When focused node has a pending artifact,
  shows the diff. Toggleable.
- **Command palette (bottom, `:` to open).** `:handoff`, `:edit`,
  `:reroute`, `:spawn <role>`, `:close`, `:save-pipeline`, etc.

### Default keys (proposed, all rebindable)

- `Ctrl-b` then `↑/↓/←/→` — focus node
- `F2` — handoff focused node to next in pipeline
- `Shift-F2` — handoff with edit-artifact step
- `F3` — open artifact in `$EDITOR`
- `F4` — reroute (pick destination)
- `F5` — spawn new node
- `:` — command palette

### Typical flow

1. `term` in your repo. TUI opens, no nodes yet.
2. `:spawn spec-writer` → worktree created, pane opens with `claude` running.
3. Talk to it interactively until SPEC.md looks right.
4. `:spawn reviewer` (any agent) — pane opens, idle.
5. `F2` from spec-writer → reviewer. Diff appears in tray, reviewer's pane
   gets prompted with the spec. Reviewer writes REVIEW.md.
6. `:spawn builder`, `F2` from reviewer → builder. Builder sees both
   SPEC.md and REVIEW.md, makes the changes.
7. Merge builder's branch back to `main` when satisfied.

## Stack

- **Language:** Python 3.11+. Fastest path to a working TUI; the
  performance-sensitive part is rendering, which Textual handles.
- **TUI:** [Textual](https://textual.textualize.io/). Good widget model,
  CSS-ish styling, decent terminal emulation primitives.
- **PTY:** `ptyprocess` for spawning, plus a small terminal-emulator
  widget that renders the child's ANSI output. Textual's built-in
  `Terminal` widget is the first thing to try; fall back to embedding
  `pyte` if needed.
- **Git:** shell out to `git` directly. No pygit2 / GitPython. Keeps
  errors legible.
- **Config:** TOML. `~/.config/term/config.toml` for user-level agent
  recipes, `<workspace>/.term/pipeline.toml` for per-project pipelines.
- **Editor integration:** `$EDITOR` for in-flight artifact editing.

## Config sketch

`~/.config/term/config.toml`:

```toml
[agents.claude-code]
command = "claude"
one_shot = ["claude", "-p", "{prompt}"]

[agents.codex]
command = "codex"
one_shot = ["codex", "exec", "{prompt}"]

[roles.spec-writer]
agent = "claude-code"
prompt_template = """
You are writing a specification. Produce a clear SPEC.md in this worktree
based on the following request:

{input}
"""

[roles.reviewer]
agent = "codex"
prompt_template = """
Review the spec at SPEC.md (just delivered into this worktree). Write your
review to REVIEW.md, flagging gaps, ambiguities, and risks.
"""

[roles.builder]
agent = "claude-code"
prompt_template = """
Implement SPEC.md, addressing REVIEW.md. Make the changes directly in this
worktree.
"""
```

`<workspace>/.term/pipeline.toml`:

```toml
name = "spec-review-build"
nodes = [
  { role = "spec-writer", mode = "persistent" },
  { role = "reviewer",    mode = "one-shot"   },
  { role = "builder",     mode = "persistent" },
]
```

## v1 scope

In:

- Workspace = current git repo (or `term init` scaffolds one).
- Linear pipelines defined in TOML, loaded at startup; `:spawn` to add
  nodes dynamically.
- Persistent and one-shot node modes.
- Cherry-pick handoff with editor-based artifact interjection.
- Pipeline sidebar, active PTY pane, diff viewer tray.
- Command palette with the actions above.
- Agent recipes for `claude` and `codex`; user can add more in config.

Deferred:

- DAG pipelines / branching / fan-out / fan-in.
- Auto-relay / autonomous loops.
- Conflict-resolution UI beyond "drop into pane and `git status`".
- Headless mode (`term pipeline run <name>`).
- Recording / replay of a pipeline run.
- Cost/token tracking.
- Per-node model selection UI (config-level only in v1).

## Open questions

1. **Worktree cleanup.** When a node closes, do we keep its branch
   around (audit trail) or prune? Lean toward keep + manual prune.
2. **Editor of choice.** `$EDITOR` is the default, but a built-in
   buffer might be nicer for short artifact edits. v1 = `$EDITOR`, soft
   plan to add inline later.
3. **What counts as "the artifact"** when an agent has touched 30 files
   and only 3 are real? Default to the full worktree diff since the last
   handoff; let the user `F3` to prune before sending. Better than guessing.
4. **Naming.** "term" is the repo name; might want a less generic
   product name before going public. Not blocking.
5. **Handoff semantics when target branch has diverged.** Cherry-pick
   may conflict. v1: surface clearly, pause, let user resolve in B's
   worktree. v2: smarter merge strategies.
6. **PTY widget reliability.** Real-world Claude Code / Codex TUIs are
   ANSI-heavy. Need to validate that whichever terminal-emulator widget
   we use renders them cleanly without flicker. This is the single biggest
   technical risk.

## Build order (rough)

1. PTY pane widget that can host `claude` cleanly. *Validate the risk first.*
2. Workspace + worktree primitives (`term init`, spawn worktree).
3. Pipeline + role config loading.
4. One persistent node, end-to-end: spawn, type, observe.
5. Two persistent nodes + manual `F2` handoff with cherry-pick.
6. Diff viewer tray.
7. `$EDITOR` artifact interjection.
8. One-shot nodes.
9. Command palette + dynamic spawn/reroute.
10. Polish: keybindings, status states, error UX.
