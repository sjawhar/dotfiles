---
name: taking-a-new-request
description: Use when a person, lead or parent agent asks mid-task for more than your goal ("new task/job", "also do X", "while you're at it", new direction, pasted link or error needing its own fix) even if subjects or files look alike, or to say what a running session can absorb. Not a re-review or next round.
---

# Taking a new request while working

Nothing uncovered stays only in this session's head: a request folded into running work gets done only where it overlaps, and the rest disappears without anyone writing it down.

## Steps

1. **Classify the request.** Say in one line whether the request is new work or part of the running work.
   - Same files is not the same goal.
   - The request is part of the running work only when it shares that work's goal. Otherwise it is new work.
2. **List the requirements.** Write one line per requirement of the request: the requirement as the person stated it, and whether the running work covers it.
3. **Task or ask.** Before the session continues, give each uncovered requirement its own task, or ask the person when you cannot tell what it needs or who should do it.
   - A task is a fresh agent started with the requirement as its goal (the command is below).
   - File a Dispatch issue for the requirement first when it needs one (`dispatch issue`).
   - When you file a Dispatch issue for the requirement, start the agent on its key.
   - A line in a todo list or a plan, or an issue no agent works on, is not a task.
   - A requirement drops only on the person's own answer, comment or approved change of the request. Silence, a reaction or your own reading of it drops nothing.
4. **Report.** In the reply that accepts the request, report the split: what this session now covers, what went to a fresh agent or task (with its id), and what waits on an answer.

## When the person folds the work into this session

- Do steps 1 to 3 anyway.
- Then name what this session cannot cover.
- Offer a fresh agent for it with the command that starts it.
- This session cannot cover a requirement that:
  - needs another repository or branch;
  - blocks on something this session waits for;
  - or this session could not finish together with the running work, so the running work would end this session undelivered.
- Do not accept the whole request silently and deliver only the overlap.

A stream is a line of work with its own stream issue, the open Dispatch issue labelled `stream`, and a lead session in its own tmux window. The command, by situation:

| Situation | Command |
| --- | --- |
| The request has a Dispatch issue | `agent-new <issue key>` |
| The request has no Dispatch issue | `agent-new @brief.md` or `agent-new "<request>"` |
| To start it in its own worktree | Add `--repo <repo> --name <name>` |
| The request belongs to a stream | Add `--window <that window's name>` to open the new agent as a pane beside that lead (`agent-new --help`) |
