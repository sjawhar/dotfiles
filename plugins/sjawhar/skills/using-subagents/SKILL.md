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
- Include what the subagent must NOT do (scope it out explicitly — subagents expand scope when uncertain).
- Results come back to you for verification — check them file-by-file against what you asked. A subagent report is a claim, not evidence.
- **Every dispatch carries the two shell rules, verbatim.** (1) *Never run `rm -rf` (or `rm -r`) on a path that contains a variable, `~`, or `$HOME`; `ls` the literal path first and delete that literal path.* (2) *Environment variables do not persist between your bash calls; set them per command (`env HOME=/tmp/x cmd`), never `export` in one call and rely on it in the next.* On 2026-09-04 an acceptance subagent ran `export HOME=$(mktemp -d)` in one call and `rm -rf "$HOME"` in the next; `$HOME` was `/home/ubuntu`. It destroyed the checkout holding a day of unpushed work, the opencode forks, and more before it was cancelled. Cleanup of temp fixtures belongs to the coordinator, by literal path, after the subagent reports the path.
- **Push before you move on.** A branch that exists only in a working copy is one bad command from gone. The coordinator pushes a WIP bookmark after every reviewed task; a subagent never has unpushed work older than its own task.

## Continuation beats fresh dispatch

Every `task()` result carries a continuation session ID (`ses_...`). For follow-ups, fixes, or "also do X" on the same work, continue that session (`task(task_id="ses_...")`) instead of dispatching fresh — the subagent keeps everything it already learned. The exception is a confirmed-wedged subagent: its session is the thing that is stuck, so that is the one case where a fresh dispatch (carrying a summary of what the wedged run learned) beats continuation.
