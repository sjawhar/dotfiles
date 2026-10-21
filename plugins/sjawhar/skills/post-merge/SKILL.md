---
name: post-merge
description: "Use when a PR merged: the user says 'merged' or 'PR is merged', or asks 'any cleanup or follow-up?'. Also use when a merge notification arrives for work from this session."
---

# Post-merge sweep

Do each step now. Do not list suggestions. The bar is pristine clean.

1. **Verify the merge.** Verify that the PR is merged and target-branch CI is green. If CI still runs, set a watcher and continue.
2. **Clean up your own local state.**
   - Run `git fetch`. Verify that the tracking branch is gone.
   - Delete the directory of each clone that you created for this work, by name.
   - Never run a bare `git worktree prune` on a shared store (see `disk-hygiene`).
3. **Deploy and verify live.** If the repo has a deployment step (see its AGENTS.md), run it. Examples of each trap: [verifying-live.md](references/verifying-live.md).
   - Live means observed on the NEW version. The proof must postdate the deploy, for example:
     - a scheduled tick started after the deploy's `completedAt`
     - a node or pod created after the apply began
     - the deployed image digest or squash sha
   - Key a watcher to the named apply job succeeding (for example `deploy / deploy`). Match the job name exactly, never as a substring.
   - Never key it to a run that completes, to a dev-slot `workflow_dispatch` run of the same workflow, or to the PR's head sha. A squash merge lands a different sha on main.
   - Your merge rides the newest deploy-lane run. A cancelled pending run is the queue working, not a failure. Never report it, block on it, or call it the reason the change is not live.
   - Report only a FAILED chain that carries your merge, and fix it.
   - A merged skill, plugin or dotfiles change is live only in the checkout that sessions load: `~/src/core-ops` for core-ops skills, `~/.dotfiles` for dotfiles. No deploy step is not evidence that it is live. If the checkout's `@` is empty, run `jj git fetch && jj new main@origin`, then read the served file back.
4. **Update tracking state.**
   - When the change is live, move the Dispatch issue to `done`, with a reason that names the merge and the live proof. Until then, it stays `testing`.
   - Close or update each linked GitHub issue, tracking doc and roadmap item. Plan and spec content goes in the issue body, not a file path.
   - If production is frozen, statuses do not show the merge state: `testing` looks the same for "not merged" as for "merged, waiting on the freeze". At merge time, post one message on the issue: merge sha, time, and where the change is and is not deployed (main, staging, production). Audit from artifacts and merged PRs, not statuses.
5. **Announce if the merge changes how others work.**
   - Name who it affects: persons who write to a changed convention, and users of a changed page, CLI or doc.
   - Tell them in their channel during this sweep, as part of the change. Use `speaking-as-sami` and the repo's announcement skill if it has one. If the CLAUDE.md outside-audience rule applies, draft it for Sami.
   - Internal-only merge: record "no one". Before you do, read the merged PR text for consumers outside the repository. A repo grep cannot find them.
6. **Report.** List the items that you did, or write "nothing left". Then state each remaining follow-up or cleanup in full sentences with context, not bare issue numbers or shorthand.
