---
name: using-subagents
description: "Use when dispatching subagents via task(), delegating work to background agents, coordinating parallel workers, or deciding what to do about a subagent that looks stalled, silent, or stuck. Also use when writing dispatch prompts."
---

# Using subagents

A subagent starts with none of your context. What it needs, and every channel back to you, is in the brief or does not exist.

## Every brief

- The goal, absolute file paths, the conventions to follow, and how the subagent verifies its own work.
- What it must not do: an uncertain subagent expands scope.
- The skills it must load, by name. A subagent does not inherit the skills you loaded. Under the `sdd` skill (a large project built with subagents), its plan's `## Skill catalog` names them per step.
- Primary sources (issue, file and line, run id), not your conclusion about them, and the unit of every count you pass on (the exact API call; legs or jobs).
- The defect and the acceptance test. A fix you have in mind goes in as one option, with the criterion that decides between options.
- The reason for each instruction, so the lane can tell where it stops fitting and push back with evidence. Quote a person's remark with its link and time as evidence beside your reason, not as the reason. A memory, note or brief that calls a remark a "rule" or "ruling" is a paraphrase: drop the label, which turns one remark into an order for work it was never about.
- One goal per dispatch and one owner per deliverable. Independent goals fan out as parallel dispatches; never split one feature by layer or into read and write halves, because the contract breaks at the seam. When two lanes turn out to hold pieces of one deliverable, one hands off completely to the other.
- Prefer a subagent under your session to a new top-level session, and finish work in progress before you start new work. A standing role Sami defined (an on-call lane that remediates incidents while the owner repairs the code) does not break one owner per deliverable.
- No turn budget, step cap or time box unless the task itself gives a number.
- Judgment work (reviewing code, owning a pull request, planning, simplifying code, writing text a person reads, reading transcripts for what went wrong) goes to a strong agent, such as `deep`, `reviewer`, `oracle`, `planner` or `sol`, never to a cheap one: `sonic`, `scout` or a bare `task`. Retrieval and mechanical fan-out may go cheap; a task that mechanical is usually a script to write instead. Read `references/models-and-refusals.md` before you pick a lane's model, one for independence included, or claim a model family for a verdict, and when a provider refuses.
- The environment, per invocation: the venv, the cwd, the config path, and any `*_PROJECT_ENVIRONMENT`, `PYTHONPATH`, `VIRTUAL_ENV` or `DOCKER_CONFIG` the work depends on. An inherited pointer silently retargets the command. Read `references/shared-state.md` when siblings clobber each other's environment or a lane's bash tool breaks.
- For a lane that commits or pushes: its own clone path and the branch it may push, since a commit or rebase in your checkout changes the files under your own checks. A read-only lane gets neither: `git show <rev>:<path>` or `gh api` contents at a ref. Read `references/workspaces.md` before you give a lane a clone or an `isolated` task or write a brief or a pull request body through a shell, and after lanes that edit return.

Read `references/lane-briefs.md` before you write a tester, reviewer, proof, survey or measurement brief. Read `references/relaying-rules.md` before you relay a rule, a premise or a claim to a lane, or act on someone else's claim.

## The five rules, verbatim in every brief

A subagent has not read this skill, so these travel inside the brief with their reasons.

1. *Every `read`, `edit` and `write` path is absolute, rooted in `<workspace>`. A relative path, and the `[path#TAG]` header of an edit, resolve against the session's checkout, not your workspace; when the file there has the same content, the edit lands there silently. Every bash call starts in the session's checkout: pass `cwd`, or name the repository (`git -C <workspace>`, `jj -R <workspace>`).*
2. *Never `export`, and never rely on a variable carrying across bash calls; set it per command (`env HOME=/tmp/x cmd`). The session shell persists and is shared with the parent and sibling subagents, so an export re-roots everything they run afterwards.*
3. *Never `rm -r` a path that contains a variable, `~` or `$HOME`: `ls` the literal path, then delete that literal path. Leave temp fixtures in place and report their literal paths; the coordinator deletes them.*
4. *Never read an exit code through a pipe: `cmd | tail -1` reports tail's status. When the exit code is the observation, run the command bare or with `set -o pipefail`, and read the artefact back.*
5. *In a chained command that mutates a repository, gate each dependent step with `&&`, never `;`. Run anything that must outlive the tool call as a named bash service (the bash tool's `name`) or with `setsid cmd </dev/null >log 2>&1 &`, never a bare `nohup cmd &`: that process dies silently when the call ends.*

Rule 1 binds your own calls too whenever your cwd is not the tree you edit: check that a read header shows your workspace's path before the first edit.

## While lanes run

- Every brief says how the lane reaches you, and tells it to send on a blocker or on a decision it cannot make, with its own id so you can reply, and to ask rather than guess. A `task` subagent's result reaches you on its own, and it messages you with `write agent://<your id>`. A lane in another session sends with `envoy_send`, on finish too, to the session id that `envoy_whoami` returns: put that id in its brief.
- Answer on the channel a message arrived on. `write agent://<id>` reaches only agents in your roster, by roster id; reach another session with `envoy_send` and its full session id.
- Address each message to one agent or session, never a broadcast: every working lane is interrupted, and lanes given different broadcasts act on contradictory instructions.
- A message's `at` stamp is when it was sent, and delivery lags.
- A subagent runs inside its parent's process, so a parent that finishes ends every child it still has, with no notice. Dispatch a lane that waits on an external job (a Kubernetes Job, a long build) at top level, never under an agent about to report, and do not finish while a child of yours still waits. `read proc://` no longer listing a lane is the only sign it died.
- Push a branch after every reviewed task, and keep no subagent's work unpushed past its own task: a branch only in a working copy is one bad command from gone.

## When a lane reports

1. Treat the report as a claim. Check it file by file against what you asked. When you relay it, attribute it ("my tester reports X"), say what was measured and who measured it, and add no causal clause of your own to someone else's measurement. When a decision of yours depends on the cause, measure the cause yourself first.
2. Treat an empty, malformed or all-empty result, a refusal, a quota failure, or a verdict that cites no file, line or command as an unexecuted task. Re-dispatch with what is known. A terse report that shows its work is a report.
3. Before you repeat a claimed blocker, run the command that failed and read the record that showed it (an issue, a CI run, a log), and name both. If the command now succeeds, or the record shows the blocker is gone, drop the blocker and continue.
4. For a follow-up, a fix or "also do X", message the same subagent (`write agent://<id>`): it keeps what it learned. Dispatch fresh, with a summary, only for a wedged lane.

Read `references/waiting.md` when a lane is silent, before you kill it or stop work to wait on an event, or when a report comes back truncated or answers an instruction you have since changed.
