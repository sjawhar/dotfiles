# Repository merge rules

Read before you merge in a repository named below, change an enabled auto-merge, or merge a PR that adds or changes a database migration.

## Changing an enabled auto-merge

To change the sha or message auto-merge uses, disable it (`gh pr merge <N> --disable-auto`), enable it again, and check that `enabledAt` (`gh pr view <N> --json autoMergeRequest`) changed: a second `--auto` exits 0 and changes nothing. A refused `--match-head-commit` means someone else pushed, so settle who owns the branch before you retry.

## Per-repository rules

| Repo | Merge rule |
|---|---|
| `trajectory-labs-pbc/agent-c` | At the head: `pr-checks-result` and `review` (workflow "Claude PR Review", run from main's copy); also `checklist-affirmation` (`docs/pre-pr-checklists.md`) when the org ruleset lists it as required, so read the ruleset (`gh api repos/trajectory-labs-pbc/agent-c/rules/branches/main`) before you count it. The org ruleset enforces these. No approval needed. |
| `sjawhar/legion` | One approving review from someone other than the author. Legion merges the work it produces. A PR that a devbox agent opens there gets a full review at its head; then someone other than its author approves and merges it. Measured 2026-10-25 over recent closed PRs: `legion-reviewer[bot]` gave 27 of 34 approvals, so an App approving there is ordinary practice, not an exception. Two routes that work: the Legion review App, or `GITHUB_PERSONAL_ADMIN_PAT` from the Legion secrets broker (Sami, dispatch://INFRA-430/ask/dfee8bb9-2d1a-450b-aec0-20ea97a96f86: "GITHUB_PERSONAL_ADMIN_PAT is available on the legion secrets broker for this"). `GITHUB_ADMIN_PAT` is for theorem-labs repos and cannot post a review here. **Never approve your own PR** — with the review App key (`GH_REVIEW_APP_PRIVATE_KEY_B64`) or anything else. That is the whole of the ban: Sami, dispatch://INFRA-155/ask/3d86f1b6, answering "the landing-a-pr skill tells agents to approve their own sjawhar/legion pull requests with that key" with "It should not say that". An approval survives a push, so a push after approval needs a new review and approval at the new head. |
| Repo another person maintains | The maintainer is not our quality gate. Send a PR only after our review passes at its head and its benefit is measured on their setup. Merge only a change that helps them, after one short note to them. Close the rest. Never hand it to them to judge. |
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
