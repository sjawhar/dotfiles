---
name: taking-a-new-request
description: 'Use when a mid-task message from a person, lead or parent agent asks for work beyond your current goal ("new task", "also do X", "while you''re at it", an added requirement, a pasted link or error), or you are tempted to fold it in because the files look alike. Not for a re-review.'
---

# Taking a new request while working

A request folded into running work gets done only where it overlaps, and the rest disappears without anyone writing it down.

## Steps

1. Say in one line whether the request is new work or part of the running work. Same files is not the same goal.
2. Write one line per requirement of the request: the requirement as the person stated it, and whether the running work covers it.
3. Before the session continues, give each uncovered requirement its own task (best a fresh agent with the requirement as its goal), or ask the person when you cannot tell what it needs or who should do it. A line in a todo list or a plan is not a task. A requirement drops only on the person's own answer, comment or approved change of the request; silence or a reaction drops nothing.
4. Report the split in the reply that accepts the request: what this session now covers, what went to a fresh agent or task (with its id), and what waits on an answer.

## When the person folds the work into this session

Do steps 1 to 3 anyway. Name what this session cannot cover (it needs another repository or branch, it blocks on something this session waits for, or it would push the running work past what it can finish), and offer a fresh agent for it, with the command that starts it: `agent-new <issue key>`, `agent-new @brief.md` or `agent-new "<request>"`, adding `--repo <repo> --name <name>` for its own worktree (`agent-new --help`; `scripts/AGENTS.md` has the rest).
