---
name: post-merge
description: "Use when a PR merged: the user says 'merged' or 'PR is merged', or asks 'any cleanup or follow-up?'. Also use when a merge notification arrives for work from this session."
---

# Post-merge sweep

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Do every step now, until nothing is left over: a step left as a suggestion gets done by nobody.

1. **Verify the merge.** Check that the PR is merged and target-branch CI is green. If CI still runs, start a background watcher and keep working.
2. **Clean up your own local state.**
   - Run `git fetch` and check that the tracking branch is gone.
   - Delete each clone or worktree you created for this work, by name. A bare `git worktree prune` on a shared store deletes other sessions' worktrees (`skill://disk-hygiene`).
3. **Deploy and verify live.** If the repo's AGENTS.md gives a deployment step, run it. Traps: [verifying-live.md](references/verifying-live.md).
   - Live means observed on the NEW version: the proof must postdate the deploy. A scheduled run started after the deploy's `completedAt`, a node or pod created after the apply began, or the service reporting the deployed image digest or squash sha.
   - Key a watcher to the named apply job succeeding (for example `deploy / deploy`), matched exactly: a substring match hits gate jobs that succeed before the apply.
   - Never key it to a run that completes (a run can finish with the apply skipped), to a manual `workflow_dispatch` run of the same workflow, or to the PR's head sha, which a squash merge replaces.
   - The newest run of the deploy workflow carries your merge. A cancelled pending run is the queue working, not a failure: never report it, block on it, or call it the reason the change is not live.
   - Report only a failed deploy run that carries your merge, and fix it.
   - A merged skill, plugin or dotfiles change is live only in the checkout that sessions load, such as `~/.dotfiles`; no deploy step tells you it is behind. Advance it (`git pull --rebase`, or `jj git fetch && jj new main@origin` where `@` is empty), then read the file back.
4. **Update tracking state.**
   - Move the Dispatch issue to `done` only once the change is live, with a reason naming the merge and the live proof. Until then it stays `testing`: `done` tells readers it is running.
   - Close or update each linked GitHub issue, tracking doc and roadmap item. Put plan and spec content in the issue body: a file path is unreadable off this machine.
   - If production is frozen, `testing` cannot tell "not merged" from "merged, waiting on the freeze": post one message on the issue with the merge sha, the time, and where the change is and is not deployed (main, staging, production). Read what is deployed from merged PRs and artifacts, not statuses.
5. **Announce if the merge changes how others work.**
   - Name who it affects: people who write to a changed convention, and users of a changed page, CLI or doc. Read the merged PR text for consumers outside the repo, which a grep cannot find.
   - Tell them in their channel during this sweep, not later. Use `speaking-as-sami`, and the repo's own announcement skill if it has one. For an outside-the-company audience, draft it for Sami, do not send (CLAUDE.md).
   - If nobody else writes or uses what changed, record "no one" in step 6.
6. **Report.** List what you did, or write "nothing left". State each remaining follow-up in full sentences with context, not a bare issue number.
