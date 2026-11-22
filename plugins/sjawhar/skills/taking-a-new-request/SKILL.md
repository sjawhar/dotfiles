---
name: taking-a-new-request
description: Use when a person, lead or parent agent asks mid-task for more than your goal ("new task/job", "also do X", "while you're at it", new direction, pasted link or error needing its own fix) even if subjects or files look alike, or to say what a running session can absorb. Not a re-review or next round.
---

# Taking a new request while working

A request folded into running work gets done only where it overlaps, and the rest disappears without anyone writing it down.

## Steps

1. Say in one line whether the request is new work or part of the running work. Same files is not the same goal: the request is part of the running work only when it shares that work's goal; otherwise it is new work.
2. Write one line per requirement of the request: the requirement as the person stated it, and whether the running work covers it.
3. Before the session continues, give each uncovered requirement its own task, or ask the person when you cannot tell what it needs or who should do it. A task is a fresh agent started with the requirement as its goal (the command is below); file a Dispatch issue for the requirement first when it needs one (`dispatch issue`), and start the agent on its key. Nothing uncovered stays only in this session's head: a line in a todo list or a plan, or an issue no agent works on, is not a task. A requirement drops only on the person's own answer, comment or approved change of the request; silence, a reaction or your own reading of it drops nothing.
4. Report the split in the reply that accepts the request: what this session now covers, what went to a fresh agent or task (with its id), and what waits on an answer.

## When the person folds the work into this session

Do steps 1 to 3 anyway. Then name what this session cannot cover, and offer a fresh agent for it with the command that starts it. This session cannot cover a requirement that needs another repository or branch, that blocks on something this session waits for, or that this session could not finish together with the running work, so the running work would end this session undelivered. Do not accept the whole request silently and deliver only the overlap.

The command is `agent-new <issue key>` when the request has a Dispatch issue, else `agent-new @brief.md` or `agent-new "<request>"`. Add `--repo <repo> --name <name>` to start it in its own worktree. When the request belongs to a stream (a line of work with its own stream issue, the open Dispatch issue labelled `stream`, and a lead session in its own tmux window), add `--window <that window's name>` to open the new agent as a pane beside that lead (`agent-new --help`).
