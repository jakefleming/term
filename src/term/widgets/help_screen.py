"""Help overlay: keybindings + palette reference."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP = """\
[b]term[/b]  — TUI for orchestrating CLI coding agents as a pipeline

[b]Keys[/b]
  Ctrl-Q      Quit
  F1          Command palette
  F2          Handoff focused node → next
  F3          Edit pending files in $EDITOR
  F4          Toggle diff tray
  F5          Toggle focus: pipeline sidebar ↔ pane
  F6          + Add agent (open spawn picker)
  F8          Toggle mouse tracking (off = native text selection)
  F9          Switch / create / delete sessions
  F12         This help screen

[b]Palette commands[/b]  (open with F1)
  spawn                              Open the spawn picker
  spawn [-o] <agent> [<role>]        Spawn directly
  handoff                            Same as F2
  handoff <node>                     Reroute: focused → <node>
  swap-agent <agent>                 Replace focused node's CLI
  swap-role <role|none>              Change focused node's prompt template
  resume [<node>]                    Resume conversation now (uses agent's
                                     resume_args, e.g. claude --continue).
                                     Term also auto-resumes on launch.
  fresh [<node>]                     Restart a node from scratch (drops the
                                     conversation auto-resume picked up)
  tell <node> <message>              Inject a message into another pane
                                     as [From <you>]: <message>. Use to
                                     relay between agents.
  brief [<node>|all]                 Prime an agent (or all) on the team
                                     comms convention so they'll actually
                                     message peers when you ask.
  session                            Same as F9
  new-session <name>                 Create + switch to a new session
  switch-session <id-or-name>        Switch to an existing session
  reset-session                      Clear the active session's saved state
  edit                               Same as F3
  rerun                              Re-trigger a one-shot node
  quit                               Exit

[b]Sessions[/b]
  A workspace can hold many sessions, each an independent pipeline with
  its own worktrees. Click the session header at the top of the sidebar
  (or press F9) to switch. On launch and on session switch, previously-
  seen agents auto-resume via their configured resume_args (e.g.
  [code]claude --continue[/code]) so conversations pick up where you left
  off. Use [code]:fresh[/code] on a node to drop history and start clean.

[b]Agent ↔ agent messaging[/b]
  Each spawn picker offers an optional [i]Name[/i] (e.g. "Alice"). The
  sidebar shows the name; messaging uses it.

  [code]:tell <name-or-id> <message>[/code] injects a message into another
  agent's pane as [code][From <sender>]: <message>[/code]. Manual relay.

  On every roster change term refreshes [code]AGENTS.md[/code] in each
  worktree, listing the team and the mailbox convention:

      ../../mail/<your-id>__to__<their-id>.txt

  Both claude-code and codex auto-read AGENTS.md in cwd, so agents
  discover their peers and the messaging contract without you having to
  brief them. Tell them once "talk to Alice when you need X" and they
  can do so autonomously. Replies arrive at your prompt as pastes.

[b]Sub-task spawn (agents spawning agents)[/b]
  Agents can request a one-shot sub-agent — e.g. claude asking opus
  for a hard call, or fan-out parallel file reads on haiku. They drop:

      ../../spawn/<their-id>__spawn__<task-id>.toml

  with fields [code]agent[/code], [code]prompt[/code], and optionally
  [code]model[/code] (opus / sonnet / haiku for claude-code; configured
  per agent in [code][agents.<name>.models][/code]), [code]role[/code],
  and [code]name[/code]. Term runs [code]agent -p "<prompt>"[/code] in
  the requester's worktree, caps concurrency at 5, captures stdout, and
  delivers the result to the requester's pane prefixed
  [code][sub-task done · ...][/code]. The sidebar Tasks section shows
  in-flight subs — click a task row to open the inspector with the
  prompt and live stdout stream.

[b]Concepts[/b]
  A node = agent (CLI to run) + optional role (prompt template) + mode
  (persistent / one-shot). Agent and role are independent — same agent can
  play any role, same role can run on any agent.

  Each node owns a [i]git worktree[/i] under .term/worktrees/<node-id> on
  branch term/node/<node-id>. Handoff snapshots source's tracked files
  into target via [code]git archive HEAD | tar[/code], commits in target,
  injects the target role's prompt template (if any).

[b]--yolo / --dangerously-skip-permissions[/b]
  Pass either flag when launching term to opt-in for the session. Each
  agent's [code]yolo_args[/code] (from defaults / ~/.config/term/config.toml)
  are appended to its command. Bundled: claude-code gets
  [code]--dangerously-skip-permissions[/code].

[b]Status markers[/b] in the sidebar
  ·   idle           ●   running         ✓   ready
  ✗   blocked        ⏹   child exited

[dim]press esc, q, or F12 to close[/dim]
"""


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > VerticalScroll {
        width: 80%;
        max-width: 90;
        height: 80%;
        background: $surface;
        border: heavy $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape,q,f12,question_mark", "pop", "Close", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(_HELP)

    def action_pop(self) -> None:
        self.app.pop_screen()
