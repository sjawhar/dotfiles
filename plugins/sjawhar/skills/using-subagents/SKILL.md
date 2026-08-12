---
name: using-subagents
description: "Use when dispatching subagents via task(), delegating work to background agents, coordinating parallel workers, or deciding what to do about a subagent that looks stalled, silent, or stuck. Also use when writing dispatch prompts — the comms contract belongs in every prompt."
---

# Using Subagents

Subagents lose your context the moment they start. Everything they need — and every channel back to you — goes in the dispatch prompt, or it does not exist.

## Envoy comms (check once per session)

Run `envoy_whoami`. If it returns a session ID, Envoy is available and the rest of this section is mandatory; if the tool is missing, skip to Dispatch Prompts.

**In every dispatch prompt, include:**

1. Your session ID, with the instruction: *message me via `envoy_send(target_session="<your-id>", ...)` when you finish, hit a blocker, or need a decision you cannot make yourself. Include your own session ID (from `envoy_whoami`) so I can reply.*
2. That a clarification question sent this way beats guessing. A subagent that guessed wrong burns its whole run; one that asked lost a minute.

**Stalled subagent? Message it before you kill it.** A subagent that looks stuck may be deep in legitimate work — killing it burns everything it learned. `envoy_send` a status request and give it ~10 minutes to answer (longer if its task involves builds or long test suites); re-dispatch only on silence or a reply confirming it is wedged. Kill-then-redispatch without asking is the last resort, not the reflex.

**Replies arrive as turns in your session** with a reply-to session ID — respond with `envoy_send(target_session="<their-id>", ...)`.

## Dispatch prompts

- State the goal, the exact file paths (absolute — the subagent's working directory may not be yours), the conventions to follow, and how the subagent should verify its own work. "Fix the bug" prompts produce guesses.
- One goal per dispatch. Multiple independent goals fan out as parallel dispatches, never bundled.
- Include what the subagent must NOT do (scope it out explicitly — subagents expand scope when uncertain).
- Results come back to you for verification — check them file-by-file against what you asked. A subagent report is a claim, not evidence.

## Continuation beats fresh dispatch

Every `task()` result carries a continuation session ID (`ses_...`). For follow-ups, fixes, or "also do X" on the same work, continue that session (`task(task_id="ses_...")`) instead of dispatching fresh — the subagent keeps everything it already learned. The exception is a confirmed-wedged subagent: its session is the thing that is stuck, so that is the one case where a fresh dispatch (carrying a summary of what the wedged run learned) beats continuation.
