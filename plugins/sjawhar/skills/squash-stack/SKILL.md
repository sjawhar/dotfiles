---
name: squash-stack
description: Use when a gh-stack PR stack is complete and every stacked PR has its review. Also use when asked to collapse, consolidate or squash a stack into one PR, or at the end of a multi-PR sdd plan.
---

# Squash Stack

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

The top PR becomes the consolidated PR: its branch already holds the full diff. Make no new branch and no new PR.

## Preconditions

- `gh stack view --json` lists the stack: every branch is on the remote, every PR open.
- Each PR has a reviewer agent's review. Fix a finding on the branch whose diff introduced the line, then `gh stack rebase --upstack`, so the branches above carry it.
- From an `sdd` plan: smoke test passes, final review has no open findings.

## Keep the stack ungrouped while you push

A PR grouped into a native stack (`gh stack link`) gets no `github-actions` check suites on `synchronize` or `labeled`. Let the PR bases carry the order, name each base branch in the PR body, and group only at merge time, if your merge command refuses an ungrouped stack. No check suites on a stack PR: check `gh stack view --json` for grouping before a re-run, which cannot make a suite the event never created. `gh stack unstack <n>` re-queues `PR Checks` in ~30s, with no push. If it does not, grouping was not the cause: look for another one.

## Collapse the stack

1. `gh stack view --json`: trunk, branches in order, top branch and PR.
2. `gh stack sync --prune`. Resolve any conflict and re-run.
3. `gh pr edit <top-pr> --base <trunk>`.
4. Squash and push. The request to squash the stack is the approval for this force-push.
   ```bash
   git checkout <top-branch>
   git reset --soft $(git merge-base <trunk> HEAD)
   git commit -m "<one summary of the whole change>"
   git push --force-with-lease
   ```
   Keep a series of commits only when the layers help review — a reviewer asked, or each stands alone.
5. `gh pr edit <top-pr>`: title and body for the whole change, not the last slice: what the stack built, each consolidated PR (number, link, review outcome), and the `## Verification` section from `opening-a-pr`.
6. Close each base PR and keep its branch, whose commits its review comments point at: `gh pr close <N> --comment "Consolidated into #<top-pr>; the reviewed diff lives there."`
7. `gh stack unstack`: it removes the local tracking and the GitHub grouping, not the branches or PRs.

`gh stack` has no collapse command. Never use `gh stack merge --squash`: it merges the stack into the trunk, skipping the consolidated review.

## After approval

When Sami approves, squash-merge it yourself; never hand the PR back. No admin merge or branch-protection bypass, except on `landing-a-pr`'s terms: it merges a diff whose checks never ran. Then run `post-merge`.
