---
name: using-subagents
description: "Use when dispatching subagents via task(), delegating work to background agents, coordinating parallel workers, or deciding what to do about a subagent that looks stalled, silent, or stuck. Also use when writing dispatch prompts."
---

# Using Subagents

A subagent starts with none of your context. Everything it needs, and every channel back to you, is in the brief or does not exist.

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Every brief

- The goal, the absolute file paths, the conventions to follow, and how the subagent verifies its own work. "Fix the bug" briefs produce guesses.
- What the subagent must not do, spelled out: subagents expand scope when they are uncertain.
- The skills it must load. A subagent does not inherit the skills you loaded, so a skill not named in its brief does not exist for it. Under `sdd` the plan's `## Skill catalog` names them per step.
- Primary-source pointers — issue number, file and line, run id — not your conclusion about them: your conclusion is the thing most likely to be wrong, and the worker's independent read is the cheapest check in the system. State the unit of every count you pass on (the exact API call, legs versus jobs), because two workers who read one endpoint correctly but count different units report numbers that look contradictory.
- The defect and the acceptance test. A fix you have in mind goes in as one candidate ("one option: …") with the criterion that decides between options, because a worker that treats your guess as the spec stops measuring.
- One goal per dispatch, and one owner per deliverable. Independent goals fan out as parallel dispatches; a feature or fix is never split by layer or "read half / write half", because the seam between the halves is where the contract goes wrong. When two lanes turn out to hold pieces of one deliverable, one hands off completely to the other. Prefer a subagent under your own session to a new peer session, because every extra top-level session is one more Sami has to watch.
- No turn budget, step cap or time box, unless the task itself gives a number (an eval that caps turns is the task's spec). Sami lets agents run for days, and a cap leaves no room for the subagent's own subagents.
- Judgment work — reviewing code, owning a PR (fork or not), planning, simplifying code, writing or rewriting something a human will read, reading transcripts for what went wrong — goes to a strong model; retrieval and mechanical fan-out may go cheap (`references/models-and-refusals.md`).
- The environment, named per invocation: the venv, the cwd, the config path, and any `*_PROJECT_ENVIRONMENT`, `PYTHONPATH`, `VIRTUAL_ENV` or `DOCKER_CONFIG` the work depends on, since an inherited pointer silently retargets the command (`references/shared-state.md`).
- A clone path of its own, and the branch it may push, for any lane that will commit or push: a subagent's `git commit`, `git rebase` or `git push` in your checkout changes the files under the tests or checks you are running there. A read-only lane gets neither — `git show <rev>:<path>` or `gh api` contents at the ref read fine from what already exists.
- A real filesystem path for any file you ask for, since a dispatched agent's write tool refuses `/tmp/omp-local/session` URIs.

## The five rules, verbatim in every brief

A dispatched `sonic` loads no skills and its tools default to the shared checkout whatever workspace it was given, so these travel inside the brief, mechanism included — a paraphrase drops the part an agent with no skills cannot reconstruct.

1. *Every `read`, `edit` and `write` path is absolute. A relative path resolves against the session's own checkout, not your workspace, and an `edit` header whose tag matches an identical-content file there edits that file silently. Every bash call starts in the session's checkout, so every command that touches a repository begins `cd <workspace> &&` or names it (`git -C <workspace>`, `jj -R <workspace>`), and that checkout may be another lane's live one.*
2. *Never `export`, and never rely on a variable carrying across bash calls in either direction; set it per command (`env HOME=/tmp/x cmd`).* An export survives into every later bash call and reaches the parent and sibling subagents, so `export HOME=$(mktemp -d)` re-roots everything the session runs afterwards, and a leaked `GIT_DIR` or `GH_TOKEN` overrides the `-C` or `--profile` flag you trust to scope a command.
3. *Never run `rm -rf` (or `rm -r`) on a path that contains a variable, `~` or `$HOME`; `ls` the literal path first and delete that literal path. Leave temp fixtures in place and report their literal paths; the coordinator deletes them.* Reason: if an earlier `export HOME=…` did not carry into this bash call, `$HOME` is the real home directory and `rm -rf "$HOME"` deletes it.
4. *Never read an exit code through a pipe: `cmd | tail -1` reports tail's status, not cmd's. When the exit code is the observation, run the command bare, or use `set -o pipefail` or `${PIPESTATUS[0]}`, and read the artefact back.*
5. *In any chained command that mutates a repository, gate dependent steps with `&&`, never `;`. Anything that must outlive the tool call runs under `hub start`, never a bare `nohup cmd &`; where `hub start` fails, use `setsid cmd </dev/null >log 2>&1 &`.* A `;` runs the push after the failed edit, and a bare `nohup` process dies silently when the call ends (`references/shared-state.md`).

Rule 1 binds your own tool calls whenever your session's cwd is not the tree you are editing. Confirm the read header shows your workspace's path before the first edit, and after the subagents you dispatched return, check your own checkout for stray files (`references/workspaces.md`).

## While lanes run

Run `envoy_whoami` once. If it returns a session ID, every brief carries your session ID with the instruction to `envoy_send(session_id="<your-id>", …)` on finish, blocker or a decision it cannot make, including its own id so you can reply; and the line that asking a clarification beats guessing, since a wrong guess burns the whole run and a question costs a minute. Replies arrive as turns in your session with a reply-to id, and you answer on the channel a message arrived on (`references/waiting.md`).

Message a lane that looks stalled before you kill it, and give it about 10 minutes to answer: killing it burns everything it learned.

Push a branch after every reviewed task, and keep no subagent's work unpushed past its own task: a branch that exists only in a working copy is one bad command from gone.

## When a lane reports

- A subagent report that names the evidence for each claim (command output, a file and line, a diff) is evidence: check it file by file against what you asked, and redo none of its work. A claim with no evidence is a claim; get the evidence before you act on it. When you relay one, attribute it ("my tester reports X"), and do not attach your own causal clause to someone else's measurement — say what was measured and who measured it, and measure the cause yourself if it matters.
- Read structured output field by field (`agent://<id>/findings/0/title`): reading `agent://<id>` whole cuts long lines at 768 characters, so parsing it as JSON fails.
- An empty, malformed or schema-valid-but-all-empty result is an unexecuted task, not a clean verdict; so is a provider refusal or a quota failure — only a parsed verdict is a verdict. Read the completion status and the values; a verdict citing no file, line or command is unexecuted. The trigger is zero content or a failure status, not brevity: a terse report that shows its work is a report. Re-dispatch carrying what is known.
- Recheck a claimed blocker before repeating it: name the exact command that still fails and the record you checked. If the command now succeeds, or the record shows the blocker is gone, drop the blocker and continue the task.
- Continue the session (`task(task_id="ses_…")`) for a follow-up, fix or "also do X" — it keeps everything it learned. The one exception is a wedged subagent — one that stayed silent through the status-message wait in `references/waiting.md`, or answered that it is stuck — where a fresh dispatch carrying a summary beats continuation.

## References

- `references/lane-briefs.md` — before writing a tester, reviewer, proof, survey or measurement brief, or relaying a rule.
- `references/models-and-refusals.md` — before picking a lane's model, when a brief names one for independence, or when a provider refuses.
- `references/shared-state.md` — the kernel, shell and environment your subagents share with you; when siblings clobber each other or a lane's bash breaks.
- `references/waiting.md` — a lane is silent, before waiting on an event, or a report came back truncated.
- `references/workspaces.md` — before giving a lane a clone, an `isolated: true` task, or a heredoc of prose.
