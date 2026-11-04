---
name: taking-a-new-request
description: 'Use when a mid-task message asks for work beyond your current goal, not a re-review or next round. Typical words: "new task", "new job", "also do X", "while you''re at it", a changed direction that adds requirements, or a pasted link or error that needs its own fix, from a person, a lead or a parent agent. Also when you are tempted to fold such a request into the running work because the subjects or files look alike, and when you must say what a running session can and cannot absorb.'
---

# Taking a new request while working

A request folded into running work gets done only where it overlaps, and the rest disappears without anyone writing it down. That is the largest single way delivered work misses its goal.

## Steps

1. Say in one line whether the request is new work or part of the work already running. Same files is not the same goal; share a goal or it is new work.
2. Write one line per requirement of the request: the requirement, and covered by the running work or not covered. Keep the person's requirement, not a smaller version of it that the running work happens to meet.
3. Each uncovered requirement gets, before the session continues: its own task, best a fresh agent with the requirement as its goal, or a question to the person when you cannot tell what it needs or who should do it. Nothing uncovered stays only in this session's head. A requirement drops only on the person's own answer, comment or approved change of the request; silence, a reaction, or your own reading of it drops nothing.
4. Report the split back in the same reply: what this session now covers, what went to a fresh agent or a task with its id, and what is waiting on an answer.

## When the person folds work into this session

Do steps 1 to 3 anyway, then name what this session cannot cover and offer a fresh agent for it, with the command that starts it: `agent-new <ISSUE-KEY>` for a request that has a Dispatch issue, `agent-new @brief.md` or `agent-new "<request>"` otherwise, adding `--repo <repo> --name <name>` for work in its own worktree and `--window <stream window>` to start it beside that stream's lead (`agent-new --help`). Reasons a session cannot cover a requirement: it needs a different repository or branch, it blocks on something this session waits for, or it would push the running work past what it can finish. Do not accept the whole request silently and deliver the overlap.

## Common mistakes

| Mistake | Instead |
|---|---|
| "This fits what I am doing" and straight on | Write the covered/not-covered lines first; the fit is usually partial. |
| The uncovered part becomes a line in a todo list or a plan | A task someone runs, or a question to the person. |
| Reporting coverage only at the end | Say it in the reply that accepts the request. |
