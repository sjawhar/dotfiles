---
name: squash-stack
description: Use when a gh-stack PR stack is complete and every stacked PR has its review. Also use when asked to collapse, consolidate or squash a stack into one PR, or at the end of a multi-PR sdd plan.
---

# Squash Stack

The top PR of the stack becomes the consolidated PR. Make no new branch and no new PR.

## Preconditions

- `gh stack view --json` shows the stack. All branches are on the remote and all PRs are open.
- Each PR has its agent review. Fix open findings on the branch that owns them, then `gh stack rebase --upstack`.
- The `sdd` smoke test and final review are clean.

## Keep the stack ungrouped while you push

- A PR in a native stack (`gh stack link`) gets no `github-actions` check suites on `synchronize` or `labeled`. `gh stack unstack <n>` re-queues `PR Checks` within about 30 seconds, with no new push. If it doesn't, look for another cause. The cause is unknown, but the symptom reproduces.
- Let the PR bases carry the order, and name each base in the PR's packet. Group only at merge time, if your merge tooling needs it.
- A stack you did not create has no CI? Look for grouping before you re-run: a re-run cannot make a suite that the event did not create.

## Collapse the stack

1. `gh stack view --json`: get the trunk, the branches in order, and the top branch and PR.
2. `gh stack sync --prune`.
3. `gh pr edit <top-pr> --base <trunk>`. The top branch already holds the full diff.
4. Squash and push. The request to squash the stack is the approval for this force-push.
   ```bash
   git checkout <top-branch>
   git reset --soft $(git merge-base <trunk> HEAD)
   git commit -m "<one summary of the whole change>"
   git push --force-with-lease
   ```
   Keep a small, deliberate series of commits only when the layers really help review. Resolve conflicts as usual and continue the sequence.
5. `gh pr edit <top-pr>` with a title and body for the whole change, not the last slice. The body summarizes what the stack built, lists each consolidated PR (number, link, review outcome), and has the `## Verification` section from `opening-a-pr`.
6. Close each base PR and keep its branch: `gh pr close <N> --comment "Consolidated into #<top-pr>; the reviewed diff lives there."`
7. `gh stack unstack`. It removes tracking only.

`gh stack` has no collapse command. Never use `gh stack merge --squash`: it merges the stack into the trunk and skips the consolidated review.

## After approval

When Sami approves, squash-merge. No admin merge, no bypass of branch protection, and never give the PR back to Sami to merge. Then do the `post-merge` sweep.
