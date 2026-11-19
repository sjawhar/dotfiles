# Repository merge rules

Read before you change an enabled auto-merge, merge in a repository named below, use an admin token, or merge a PR that adds or changes a database migration.

## Changing an enabled auto-merge

To change the sha or message auto-merge uses, disable it (`gh pr merge <N> --disable-auto`), enable it again, and check that `enabledAt` (`gh pr view <N> --json autoMergeRequest`) changed: a second `--auto` exits 0 and changes nothing. A refused `--match-head-commit` means someone else pushed; settle who owns the branch before you retry.

## Whether your token can merge here

Do not decide from `gh api repos/<owner>/<repo> -q .permissions`: it reports a user's permissions and reads all false for an App installation token, whatever the installation grants. Decide by the operation: a merge your identity already made in that repository, or a dry run.

## Admin actions

A GitHub admin action that work Sami already asked for needs (an App permission or installation, an admin merge, a repository or branch setting) is yours to do with his admin token from the credential broker; do not ask him to do it. `GITHUB_ADMIN_PAT` reaches theorem-labs repositories, `GITHUB_PERSONAL_ADMIN_PAT` sjawhar's own. The broker puts the token in the variable of its own name and gh reads only `GH_TOKEN` or `GITHUB_TOKEN`, so pass it in the command:

```sh
secret-run --register --reason '<what and why>' GITHUB_ADMIN_PAT -- sh -c 'GH_TOKEN=$GITHUB_ADMIN_PAT exec gh "$@"' gh <the call>
```

- Sami approves the broker's grant card once per session. `--register` gives a session with no broker identity one, with no restart.
- The token never skips a check that tests the work.

## Approvals

- An approval of a pull request is not an admin action. No approval uses a person's token, the admin token included.
- Where a code owner owns the files changed, the approval is that person's: ask them through Dispatch, with the pull request, its head and the review verdict it got.
- You may approve a pull request your own session wrote when the work had cross-family review under `sdd` and Sami authorized the work.
- An approval counts only at the head it names: a push after approval needs a new review and approval at the new head.

## A check over a shared budget or a total

A required check that measures a budget several PRs share (a word or line count, a file or bundle size, a quota) runs on each head, not on the merged result, so each head can pass while their sum fails on the base branch. Before you enable auto-merge, measure your PR together with every open PR that touches the budgeted files, and check whether the guard runs in the merge queue's build. Put the margin in the body ("1,999 of 2,000", not "passes").

A guard that reads a diff cannot see the state: a repository can be clean on every commit and dirty in total. Where the total matters, scan the tree on a schedule as well as the diff on a push.

## trajectory-labs-pbc/agent-c

- Required at the head: `pr-checks-result` and `review` (workflow "Claude PR Review", run from main's copy). `checklist-affirmation` (`docs/pre-pr-checklists.md`) counts only when the org ruleset lists it: read `gh api repos/trajectory-labs-pbc/agent-c/rules/branches/main`. No approval is needed.
- The session or person that owns the PR merges it; no one else does.
- `review` starts only on `opened`, `synchronize` or `reopened` of a non-draft PR; marking ready starts nothing. Mark ready, then push or close and reopen, but only while no PR Checks run is in flight, because either event cancels it.
- `review` fails on an error or a refusal on both models. `claude[bot]`'s `REQUEST_CHANGES` holds the merge until it approves, and so does an unresolved thread. The merge rule reads the newest `review` check-run at the head sha; a `@claude review` comment starts nothing.
- Rerun a failed required run with `gh api -X POST repos/<owner>/<repo>/actions/runs/<id>/rerun-failed-jobs` instead of pushing; `gh run rerun` returns 404 on these. It reviews the same head and leaves PR Checks alone.
- Other PR events (`gh stack link` too) start a `follow` run that copies the newest review result. After a review rerun, rerun a newer `follow` run too (`rerun-failed-jobs`, or `.../runs/<id>/rerun` if it passed), or its stale result decides.
- Fix a red `checklist-affirmation` (the body's affirmation line) by editing the body, not by pushing.

## cybermonklabs/legion

- Work here goes to Legion, whose reviewer phase is the review; a dev-box session opens no new PR here. Legion merges the work it produces.
- GitHub needs one approving review from an identity other than the PR author's.
- A PR a dev-box session already has open gets a full review at its head from a cross-family subagent its author dispatches. The approval comes from Legion's own reviewer: ask the INFRA Legion controller (Envoy `notifications.role.legion-infra-controller`) which running Legion reviewer reads the repository, and send that reviewer the PR and its head. Ask for no review issue, and send each new head you push.
- Nothing on a dev box posts as the Legion review App.

## theorem-labs/*

- Their own required checks and reviews; approvals as in "Approvals".
- On `theorem-labs/agent-infra` the CI reviewer App (`theorem-labs-ci-review`) reviews each head, and its approval meets the merge rule: push, fix what it finds, and request no review App for your own reviewer. A cross-family review of your own is optional and posts as a comment.

## A repository another person maintains

The maintainer is not our quality gate. Send a PR only after our review passes at its head and its benefit is measured on their setup. Merge only a change that helps them, after one short note to them; close the rest. Do not hand it to them to judge.

## Other repositories

Their own required checks and reviews.

## Database migrations

Merge a PR that adds or changes a database migration by hand (`gh pr merge <N> --squash --match-head-commit <sha>`), not with auto-merge, until the repo has a merge-time migration check: two auto-merged migration PRs can leave main with two migration heads.

1. Check that main has one migration head and the PR builds on it, then merge at once.
2. If main's head moved, merge main into the branch, run `alembic merge heads`, push, and merge by hand when green.
3. Do not re-parent or renumber an existing migration.
