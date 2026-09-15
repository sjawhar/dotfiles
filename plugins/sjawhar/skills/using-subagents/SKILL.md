---
name: using-subagents
description: "Use when dispatching subagents via task(), delegating work to background agents, coordinating parallel workers, or deciding what to do about a subagent that looks stalled, silent, or stuck. Also use when writing dispatch prompts."
---

# Using Subagents

Subagents lose your context the moment they start. Everything they need — and every channel back to you — goes in the dispatch prompt, or it does not exist.

## Envoy comms (check once per session)

Run `envoy_whoami`. If it returns a session ID, Envoy is available and the rest of this section is mandatory; if the tool is missing, skip to Dispatch Prompts.

**In every dispatch prompt, include:**

1. Your session ID, with the instruction: *message me via `envoy_send(session_id="<your-id>", ...)` when you finish, hit a blocker, or need a decision you cannot make yourself. Include your own session ID (from `envoy_whoami`) so I can reply.*
2. That a clarification question sent this way beats guessing. A subagent that guessed wrong burns its whole run; one that asked lost a minute.

**Stalled subagent? Message it before you kill it.** A subagent that looks stuck may be deep in legitimate work — killing it burns everything it learned. `envoy_send` a status request and give it ~10 minutes to answer (longer if its task involves builds or long test suites); re-dispatch only on silence or a reply confirming it is wedged. Kill-then-redispatch without asking is the last resort, not the reflex.

**Replies arrive as turns in your session** with a reply-to session ID — respond with `envoy_send(session_id="<their-id>", ...)`.

## Dispatch prompts

- State the goal, the exact file paths (absolute — the subagent's working directory may not be yours), the conventions to follow, and how the subagent should verify its own work. "Fix the bug" prompts produce guesses.
- One goal per dispatch. Multiple independent goals fan out as parallel dispatches, never bundled.
- **Absolute paths are the mechanism, not style: a subagent's relative tool paths resolve against the PARENT's cwd.** `edit`/`read`/`write` with a relative path act in the coordinator's working directory even when the subagent holds its own `jj workspace add` path; a subagent told to work in `../.worktrees/x` that edits `meta/foo.py` edits the coordinator's live checkout. Verified 2026-09-13 on agent-c #18341: five investigators, each with its own workspace, wrote stray files into the coordinator's checkout and contaminated two PR heads. Every path in a dispatch is absolute and rooted in the subagent's own workspace; the brief says "absolute paths only" in those words; a coordinator whose working copy is live either does that or dispatches from a scratch cwd. After a dispatch wave returns, `jj st` in your own checkout - a stray file there is the subagent's.
- Include what the subagent must NOT do (scope it out explicitly — subagents expand scope when uncertain).
- Results come back to you for verification — check them file-by-file against what you asked. A subagent report is a claim, not evidence.
- **Every dispatch carries the two shell rules, verbatim.** (1) *Never run `rm -rf` (or `rm -r`) on a path that contains a variable, `~`, or `$HOME`; `ls` the literal path first and delete that literal path.* (2) *Environment variables do not persist between your bash calls; set them per command (`env HOME=/tmp/x cmd`), never `export` in one call and rely on it in the next.* The two rules exist together: an `export HOME=$(mktemp -d)` that did not survive to the next call turns `rm -rf "$HOME"` into the deletion of the real home directory. Cleanup of temp fixtures belongs to the coordinator, by literal path, after the subagent reports the path.
- **Push before you move on.** A branch that exists only in a working copy is one bad command from gone. The coordinator pushes a WIP bookmark after every reviewed task; a subagent never has unpushed work older than its own task.
- **A subagent that will run jj mutations gets its own `jj workspace add` path in the brief.** `jj new`, `jj describe`, `jj rebase`, `jj bookmark set` all move the shared `@`; a subagent doing that in your checkout silently swaps the tree under your gate run (a coordinator's `pytest` measured the subagent's commit — `96 passed` against `113` at its own head — and passed; the jj hint was one line, "Concurrent modification detected, resolving automatically"). Name the workspace path in the brief and the bookmark it may move; a read-only subagent needs neither.
- **A claim about what X does under Y must read Y's config, not only X's source.** An oracle that reasoned from Inspect's source alone reported that hawk keeps five raw model calls per sample; hawk's `EvalSetConfig.log_model_api` defaults `True` and every raw call is retained. When the brief asks about a consumer's behaviour (hawk, a workflow, a deployment), name the consumer's config file as a required read, and treat a "found nothing" from a search scoped to one system as evidence about that system only, never as evidence of absence.

## Continuation beats fresh dispatch

Every `task()` result carries a continuation session ID (`ses_...`). For follow-ups, fixes, or "also do X" on the same work, continue that session (`task(task_id="ses_...")`) instead of dispatching fresh — the subagent keeps everything it already learned. The exception is a confirmed-wedged subagent: its session is the thing that is stuck, so that is the one case where a fresh dispatch (carrying a summary of what the wedged run learned) beats continuation.
