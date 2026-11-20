---
name: post-merge
description: "Use when a PR merged: the user says 'merged' or 'PR is merged', or asks 'any cleanup or follow-up?'. Also use when a merge notification arrives for work from this session."
---

# Post-merge sweep

Do every step now, until nothing is left over: a step left as a suggestion gets done by nobody.

1. **Verify the merge.** Read the merge state from GitHub, not from a command's exit or an armed auto-merge: `gh api repos/<owner>/<repo>/pulls/<N> --jq '{merged, merge_commit_sha}'`. Check that target-branch CI is green; if it still runs, start a background watcher and keep working.
   - A deleted branch or a missing pre-squash sha does not show whether work merged: read the destination's content (`jj file show -r <destination> <path>`) before you redo work. The GitHub PR-head ref can keep the PR's commits reachable; check it before you rely on it.
   - Read the merged message (`gh api repos/<owner>/<repo>/commits/<merge-sha> --jq .commit.message`): squash defaults can join old subjects, retracted claims too. Do not rewrite merged history.
2. **Clean up your own local state.**
   - Run `git fetch` and check that the tracking branch is gone.
   - Remove each clone, worktree or jj workspace you created for this work, by name. Do not run a bare `git worktree prune` on a shared store: it deletes other sessions' worktrees (`disk-hygiene` has the shared-store rules).
3. **Deploy and verify live.** If the repo's `AGENTS.md`, `CLAUDE.md` or `README.md`, or a deploy doc one of them names, gives a deployment step, run it.
   - Live means observed on the new version, so the proof must postdate the deploy: a scheduled run started after the deploy's `completedAt` (one in flight during the apply can finish on the old definition), a node or pod created after the apply began, or the service reporting the deployed image digest or squash sha. For installed software, check the version the consumer runs by a head-specific artifact or behavior, not a guessed string in a minified bundle; an install the change needs is part of the work.
   - Then run the changed path in production the way a user reaches it (same URL, CLI or API, same credentials), and record the deployed version and result on the PR. To tell versions apart without writing anything, send an atomic batch whose first step the new version rejects and whose second step always fails on the old one; check the ordering and predicted errors first.
   - The newest deploy run started after your merge carries it. Confirm by ancestry: after you fetch both objects, `jj log -r '<merge-sha> & ::<run-head>'` returns the merge. Pin that run for the wait.
   - Key a watcher to the named apply job succeeding (for example `deploy / deploy`), matched exactly. A reusable workflow prefixes each child job with its own name, so a substring match hits gate jobs (`production-apply / wait-for-apply-images`) that succeed before the apply.
   - Do not key it to a run that completes (a run can finish with the apply skipped), to a manual `workflow_dispatch` run of the same workflow, or to the PR's head sha, which a squash merge replaces.
   - A deploy workflow with a concurrency group holds one running and one pending run, and a newer merge replaces the pending one. A cancelled pending run deployed nothing and its merges ride the newest run: re-resolve by ancestry, and do not report it, block on it, or call it the reason the change is not live.
   - Report only a failed deploy run that carries your merge: diagnose it at the named job and step, and fix it.
   - A merged skill, plugin or dotfiles change is live only in the checkout that sessions load, and no deploy step says it is behind. Advance it. In `~/.dotfiles`, run `scripts/dotfiles-sync local`, not a bare `jj rebase`: a rebase that conflicts writes conflict markers into the tree every session reads, and `dotfiles-sync` refuses a conflict before any file on disk changes. In another jj checkout, run `jj git fetch && jj rebase -r @ -d main@origin` and stop on a conflict. In a plain git checkout, run `git pull --rebase`. Then read the served file back.
4. **Update tracking state.**
   - Move the work's Dispatch issue to `done` only once the change is live, with a reason naming the merge and the live proof: `dispatch issue-update --issue <KEY> --status done --reason '<merge sha; live proof>'`. Until then it stays `testing`. Dispatch is the issue tracker (`skill://dispatch`); the issue is the one your session works on or the PR names. Leave the status of an issue labelled `legion`, or one under it, to the Legion daemon.
   - Close or update each linked GitHub issue, tracking doc and roadmap item. Put plan and spec content in the issue body: a file path is unreadable off this machine.
   - If production is frozen, `testing` cannot tell "not merged" from "merged, waiting on the freeze": post one message on the issue with the merge sha, the time, and where the change is and is not deployed (main, staging, production). Read what is deployed from merged PRs and artifacts, not statuses.
5. **Announce if the merge changes how others work.**
   - Name who it affects: people who write to a changed convention, and users of a changed page, CLI or doc. Read the merged PR text for consumers outside the repo, which a grep cannot find.
   - Tell them during this sweep, in a shared place where they already discuss the changed thing: the Slack channel the repo's `AGENTS.md` or `README.md` names for it, else the Slack channel, thread or issue where the change was asked for or reviewed. Use `speaking-as-sami`, and the repo's own announcement skill if it has one. For an audience outside the company, draft it for Sami; do not send it.
   - If nobody else writes or uses what changed, record "no one" in step 6.
6. **Report.** List what you did, or write "nothing left". State each remaining follow-up in full sentences with context, not a bare issue number.
