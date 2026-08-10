---
name: landing-a-pr
description: "Use after opening or updating a PR, and whenever CI results or review comments arrive on one — a failed check, a bot finding like CodeRabbit or Sentry, a human review, or a PR that has gone quiet with nothing running. Shepherds an open PR to merge-ready. Owns the whole post-open phase: review comments, CI failures, re-verification, and the merge-ready report all route through here. Sami merges; this skill never does."
---

# Landing a PR

The PR is open. Your job is to get it to genuinely merge-ready and then stop: **Sami merges.** No admin-merge, no bypassing branch protection.

1. **Watch CI and comments without blocking on them.** `gh pr checks --watch` is a fine command and a terrible foreground call — it stalls your loop for nothing. Subscribe instead (`notifications.github.<owner>.<repo>.pr.<N>.>` via Envoy delivers both check results and comments as events), or put the watch in a background PTY session with `notifyOnExit`. Then go do other work and react when something lands.
   - **No checks running at all? Look for a merge conflict first** — `gh pr view --json mergeable` — because that is usually why nothing triggered. Fix it with `jj git fetch && jj rebase -d main`, resolve, push.
   - **A check failed?** `gh run view <run-id> --log-failed`, reproduce it locally, fix the cause, push. Never iterate blind against CI. If the same check fails three times, stop repeating yourself: checkpoint what you tried, what failed, and what you learned, then either attack it a materially different way or bring Sami the options with your recommendation — never a bare "stuck."
2. **Answer every review comment, through the `receiving-code-review` skill.** Fetch both levels — `gh pr view <N> --comments` and `gh api repos/{owner}/{repo}/pulls/{N}/comments` — and treat bots (Sentry, CodeRabbit, Copilot) as reviewers. Load `receiving-code-review` and handle every comment through it: read the whole thing before reacting, verify each claim against the actual code, fix what is right, and push back with technical reasoning where the reviewer is wrong — defending the intentional choice from the plan record rather than agreeing to be agreeable. Never "note" a finding instead of fixing it. Addressing reviews is pre-authorized; do not ask whether to start.
3. **Re-verify if the diff moved.** Any push after the gate invalidates the gate. Re-run the end-to-end QA agent from `opening-a-pr` step 4 against the affected surfaces and update the PR's `## Verification` section to match. A Verification section describing a diff that no longer exists is a false claim, which is worse than none.
4. **Keep the PR record honest.** Title, description, and Verification describe the change as it stands now, not as it stood three pushes ago.
5. **Report merge-ready, with evidence.** What was run and observed end to end, CI state, which review threads are resolved. If anything is outstanding, the report says **not** merge-ready and names the specific gap. Then stop — Sami takes it from there.

Re-running this is cheap; steps that turn up nothing new get one line each.

> **jj workspace note:** in a non-default workspace there may be no `.git` directory. If `gh` fails, point it at the default workspace: `GIT_DIR=/path/to/default/.git gh ...`
