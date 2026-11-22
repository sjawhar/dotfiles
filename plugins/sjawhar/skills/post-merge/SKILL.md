---
name: post-merge
description: "Use when a PR merged: the user says 'merged' or 'PR is merged', or asks 'any cleanup or follow-up?'. Also use when a merge notification arrives for work from this session."
---

# Post-merge sweep

Do every step now, until nothing is left over: a step left as a suggestion gets done by nobody.

**Done when:** your report lists what you did, or says "nothing left", and states each remaining follow-up in full sentences with context, not a bare issue number.

1. **Verify the merge.** Read the merge state from GitHub, not from a command's exit or an armed auto-merge: `gh api repos/<owner>/<repo>/pulls/<N> --jq '{merged, merge_commit_sha}'`.
   - Check that target-branch CI is green.
   - If target-branch CI still runs, start a background watcher and keep working.
   - Read the destination's content (`jj file show -r <destination> <path>`) before you redo work: a deleted branch or a missing pre-squash sha does not show whether work merged.
   - **Check the GitHub PR-head ref before you rely on it.** It can keep the PR's commits reachable.
   - Read the merged message (`gh api repos/<owner>/<repo>/commits/<merge-sha> --jq .commit.message`): squash defaults can join old subjects, retracted claims too.
   - Do not rewrite merged history.
2. **Clean up your own local state.**
   - Run `git fetch` and check that the tracking branch is gone.
   - Remove each clone, worktree or jj workspace you created for this work, by name.
   - Do not run a bare `git worktree prune` on a shared store: it deletes other sessions' worktrees (`disk-hygiene` has the shared-store rules).
3. **Deploy and verify live.** If the repo's `AGENTS.md`, `CLAUDE.md` or `README.md`, or a deploy doc one of them names, gives a deployment step, run it.
   - The proof must postdate the deploy, because live means observed on the new version. Such proof is a scheduled run started after the deploy's `completedAt` (one in flight during the apply can finish on the old definition), a node or pod created after the apply began, or the service reporting the deployed image digest or squash sha.
   - For installed software, check the version the consumer runs by a head-specific artifact or behavior, not a guessed string in a minified bundle.
   - For installed software, an install the change needs is part of the work.
   - Then run the changed path in production the way a user reaches it (same URL, CLI or API, same credentials).
   - Record the deployed version and result on the PR.
   - To tell versions apart without writing anything, send an atomic batch whose first step the new version rejects and whose second step always fails on the old one.
   - Before you send that batch, check its ordering and predicted errors.
   - **Confirm by ancestry:** the newest deploy run started after your merge carries it. After you fetch both objects, `jj log -r '<merge-sha> & ::<run-head>'` returns the merge.
   - Pin the run that carries your merge for the wait.
   - Key a watcher to the named apply or publish job succeeding (for example `deploy / deploy`), matched exactly. A reusable workflow prefixes each child job with its own name, so a substring match hits gate jobs (`production-apply / wait-for-apply-images`) that succeed before the apply.
   - Do not key it to a run that completes (a run can finish with the apply skipped), to a manual `workflow_dispatch` run of the same workflow, or to the PR's head sha, which a squash merge replaces.
   - **Concurrency group:** a deploy workflow with a concurrency group holds one running and one pending run. A newer merge replaces the pending one.
   - Re-resolve by ancestry when a pending run is cancelled: a cancelled pending run deployed nothing, and its merges ride the newest run.
   - Do not report a cancelled pending run, block on it, or call it the reason the change is not live.
   - Read a coalescing queue's own semantics before you blame a cancellation.
   - Do not carry one workflow's semantics to another.
   - Report only a failed deploy run that carries your merge.
   - Diagnose such a run at the named job and step, and fix it.
   - Advance the checkout that sessions load for a merged skill, plugin or dotfiles change: the change is live only there, and no deploy step says it is behind.

     | Checkout | Do |
     | --- | --- |
     | `~/.dotfiles` | Run `scripts/dotfiles-sync local`, not a bare `jj rebase`: a rebase that conflicts writes conflict markers into the tree every session reads, and `dotfiles-sync` refuses a conflict before any file on disk changes |
     | Another jj checkout | Run `jj git fetch && jj rebase -r @ -d main@origin` and stop on a conflict |
     | A plain git checkout | Run `git pull --rebase` |

   - After you advance that checkout, read the served file back.
4. **Update tracking state.**

   | Term | Means |
   | --- | --- |
   | Dispatch | The issue tracker (`skill://dispatch`) |
   | The work's Dispatch issue | The one your session works on or the PR names |

   - Move the work's Dispatch issue to `done` only once the change is live, with a reason naming the merge and the live proof: `dispatch issue-update --issue <KEY> --status done --reason '<merge sha; live proof>'`.
   - Until the change is live, the issue stays `testing`.
   - Leave the status of an issue labelled `legion`, or one under it, to the Legion daemon.
   - Close or update each linked GitHub issue, tracking doc and roadmap item.
   - Put plan and spec content in the issue body: a file path is unreadable off this machine.
   - If production is frozen, post one message on the issue with the merge sha, the time, and where the change is and is not deployed (main, staging, production): `testing` cannot tell "not merged" from "merged, waiting on the freeze".
     - Read what is deployed from merged PRs and artifacts, not statuses.
5. **Announce if the merge changes how others work.**
   - Name who it affects: people who write to a changed convention, and users of a changed page, CLI or doc.
   - Read the merged PR text for consumers outside the repo, which a grep cannot find.
   - Tell them during this sweep, in a shared place where they already discuss the changed thing: the Slack channel the repo's `AGENTS.md` or `README.md` names for it, else the Slack channel, thread or issue where the change was asked for or reviewed.
   - Use `speaking-as-sami`, and the repo's own announcement skill if it has one.
   - For an audience outside the company, draft the announcement for Sami and do not send it.
   - If nobody else writes or uses what changed, record "no one" in your report.
