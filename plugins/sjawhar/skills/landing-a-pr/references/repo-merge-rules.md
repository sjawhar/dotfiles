# Repository merge rules

Read before you merge in a repository named below, change an enabled auto-merge, or merge a PR that adds or changes a database migration.

## Changing an enabled auto-merge

To change the sha or message auto-merge uses, disable it (`gh pr merge <N> --disable-auto`), enable it again, and check that `enabledAt` (`gh pr view <N> --json autoMergeRequest`) changed: a second `--auto` exits 0 and changes nothing. A refused `--match-head-commit` means someone else pushed, so settle who owns the branch before you retry.

## Whether your token can merge here

Never read `gh api repos/<owner>/<repo> -q .permissions` to decide: it reports a USER's permissions and answers all false for an app installation token whatever the installation grants, so a session that reads it concludes it is blocked and asks for a credential it already has. Check by the operation instead: a merge your identity has already made in that repository, or a dry run. A field that looks like the answer is not the field that carries it.

## When GitHub refuses the App an admin action

A GitHub admin action that work Sami already asked for needs (an App permission or installation, a CODEOWNERS approval in his name, an admin merge, a repository or branch setting) is yours to do, with his admin token from the credential broker. Never ask him to do it: he said so five times between 2026-10-25 and 2026-11-03, last on dispatch://OSSV-300/ask/dc4e1c38-bd15-4999-9ef5-4c6540a2ce9a ("You can request the admin PAT to do it yourself."). `GITHUB_ADMIN_PAT` reaches theorem-labs repositories, `GITHUB_PERSONAL_ADMIN_PAT` sjawhar's own. The broker puts the token in the variable of its own name, and gh reads only `GH_TOKEN` or `GITHUB_TOKEN`, so hand it over in the command: `secret-run --register --reason '<what and why>' GITHUB_ADMIN_PAT -- sh -c 'GH_TOKEN=$GITHUB_ADMIN_PAT exec gh "$@"' gh <the call>`. Sami approves the broker's grant card once per session; the card is not an ask. `--register` makes it work in a session that has no broker identity (one launched past `shims/omp`, where plain `secret-run` answers "not found"), with no restart; in a session that has one it changes nothing. The gh shim's `REFUSED by GitHub` block prints this command for the refused call.

## A guard over a shared budget

Where a required check measures a budget several pull requests share — a word or line count, a file or bundle size, a quota — it runs on each head and never on the merged result, so every head is green and the sum is red, and the failure lands on the base branch where no author is watching and every other lane is blocked. Measured: of four open pull requests against one 2,000-word ceiling, each passed alone at 1,999, 1,990 and 1,993, and two of them together hit exactly 2,000. Before you arm a merge, enumerate every open pull request that touches the budgeted files, not only yours, and measure the combination; a merge queue's own build is the merged result, so check whether your guard runs there or only on the pull request. Put the margin in the body, since "1,999 of 2,000" invites the next author to check and "passes" does not.

The same gap opens the other way round: a guard that reads a DIFF cannot see the STATE. One scanning added lines correctly refused a new commit message while 32 instances of the same thing already in the tree were invisible to it, so a repository can be clean on every commit and dirty in total. Where what matters is the total rather than the change, scan the tree on a schedule as well as the diff on a push.

## Per-repository rules

| Repo | Merge rule |
|---|---|
| `trajectory-labs-pbc/agent-c` | At the head: `pr-checks-result` and `review` (workflow "Claude PR Review", run from main's copy); also `checklist-affirmation` (`docs/pre-pr-checklists.md`) when the org ruleset lists it as required, so read the ruleset (`gh api repos/trajectory-labs-pbc/agent-c/rules/branches/main`) before you count it. The org ruleset enforces these. No approval needed. |
| `sjawhar/legion` | One approving review that GitHub accepts: it refuses the pull request author's own account, so the approval comes from another identity. Legion merges the work it produces. A PR that a devbox agent opens there gets a full review at its head. Measured 2026-10-25 over recent closed PRs: `legion-reviewer[bot]` gave 27 of 34 approvals, so an App approving there is ordinary practice, not an exception. Two routes that work: the Legion review App, or `GITHUB_PERSONAL_ADMIN_PAT` from the Legion secrets broker (Sami, dispatch://INFRA-430/ask/dfee8bb9-2d1a-450b-aec0-20ea97a96f86: "GITHUB_PERSONAL_ADMIN_PAT is available on the legion secrets broker for this"). `GITHUB_ADMIN_PAT` is for theorem-labs repos and cannot post a review here. **You may approve a pull request your own session wrote**, on two conditions Sami gave on 2026-10-28 (dispatch://INFRA-132/ask/3ea952b0): the work got cross-review under the SDD process, and he has generally authorized the work. His words: "Delete the rule. There is no rule about that." This replaces a line that used to read "Never approve your own PR", which paraphrased his 2026-10-24 answer on dispatch://INFRA-155/ask/3d86f1b6 into a ban he had not written; on 2026-10-28 it held agent-infra#645 and #646 for an hour. An approval survives a push, so a push after approval needs a new review and approval at the new head. |
| Repo another person maintains | The maintainer is not our quality gate. Send a PR only after our review passes at its head and its benefit is measured on their setup. Merge only a change that helps them, after one short note to them. Close the rest. Never hand it to them to judge. |
| `theorem-labs/*` | Their own required checks and reviews. When all that is left is an approval in Sami's name (CODEOWNERS) for work he already asked for, never ask him for it: approve with `GITHUB_ADMIN_PAT` and a body that says you approve for him and why (`secret-run --register --reason '<why>' GITHUB_ADMIN_PAT -- sh -c 'GH_TOKEN=$GITHUB_ADMIN_PAT exec gh "$@"' gh pr review <N> -R theorem-labs/<repo> --approve --body '<why>'`; Sami, dispatch://OSSV-159/ask/8282a4d2-cece-4faf-89d3-0dbb7fef0b37; see "When GitHub refuses the App an admin action"). The token never skips a check that tests the work. |
| Other repos | Their own required checks and reviews. |

## agent-c

- The session or person that owns the PR merges it; no one else does.
- `review` starts only on `opened`, `synchronize` or `reopened` of a non-draft PR. Marking ready starts nothing. Mark ready, then push or close and reopen, but only while no PR Checks run is in flight, because either event cancels it. A push starts a new run.
- `review` fails on an error or a refusal on both models. `claude[bot]`'s `REQUEST_CHANGES` holds the merge until it approves. An unresolved thread also holds it.
- The merge rule reads the newest `review` check-run at the head sha. A `@claude review` comment starts nothing.
- A required run failed: rerun it instead of pushing, with `gh api -X POST repos/<owner>/<repo>/actions/runs/<id>/rerun-failed-jobs`. It reviews the same head and leaves PR Checks alone. `gh run rerun` 404s on these.
- Other PR events (`gh stack link` too) start a `follow` run that copies the newest review result. After a review rerun, rerun a newer `follow` run too (`rerun-failed-jobs`, or `.../runs/<id>/rerun` if it passed), or its stale result decides.
- Fix a red `checklist-affirmation` (the body's affirmation line) by editing the body. Do not push.

## Database migrations

Merge a PR that adds or changes a database migration by hand (`gh pr merge <N> --squash --match-head-commit <sha>`), not with auto-merge, until the repo has a merge-time migration check: two auto-merged migration PRs can leave main with two migration heads. Merge right after you check that main has one migration head and the PR builds on it. If main's head moved, merge main into the branch, run `alembic merge heads`, push, and merge by hand when green. Never re-parent or renumber an existing migration.
