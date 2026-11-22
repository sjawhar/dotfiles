---
name: squash-stack
description: Use when a gh-stack PR stack is complete and every stacked PR has its review. Also use when asked to collapse, consolidate or squash a stack into one PR, or at the end of a multi-PR sdd plan.
---

# Squash Stack

The top PR becomes the consolidated PR: its branch already holds the full diff. Make no new branch and no new PR.

## Preconditions

- `gh stack view --json` lists the stack: every branch is on the remote, every PR open.
- Each PR has a reviewer agent's review.
  - Fix a finding on the branch whose diff introduced the line.
  - Then run `gh stack rebase --upstack` so the branches above carry it.
- The smoke test passes, for a stack from an `sdd` plan.
- The final review has no open findings, for a stack from an `sdd` plan.

## Keep the stack ungrouped while you push

**Why:** a PR grouped into a native stack (`gh stack link`) gets no `github-actions` check suites on `synchronize` or `labeled`. A re-run cannot create a suite the event never created.

- Let the PR bases carry the order.
- Name each base branch in the PR body.
- Group only at merge time if your merge command refuses an ungrouped stack.
- Check `gh stack view --json` for grouping if a stack PR has no check suites.
  - `gh stack unstack <n>` brings the checks back within about 30 seconds, with no push.
  - Look for another cause if the checks do not come back in that time: grouping was not the cause.

## Collapse the stack

Work in your own clone or worktree of the stack.

1. **View the stack.** Run `gh stack view --json` for the trunk, the branches in order, and the top branch and PR.
2. **Sync the stack.** Run `gh stack sync --prune`.
   - Resolve any conflict.
   - Then run it again.
3. **Base the top PR on the trunk.** Run `gh pr edit <top-pr> --base <trunk>`.
4. **Squash and push.** The request to squash the stack is the approval for this force-push.
   ```bash
   git checkout <top-branch>
   git reset --soft $(git merge-base <trunk> HEAD)
   git commit -m "<one summary of the whole change>"
   git push --force-with-lease
   ```
   - Keep a series of commits only when the layers help review: a reviewer asked for them, or each layer is one complete change whose commit builds and passes the tests without the layers above it.
5. **Describe the whole change.** Run `gh pr edit <top-pr>` with a title and body for the whole change, not the last slice: what the stack built, each consolidated PR (number, link, review outcome), and the `## Verification` section from `opening-a-pr`.
6. **Close each base PR.** Run `gh pr close <N> --comment "Consolidated into #<top-pr>; the reviewed diff lives there."`
   - Keep each base PR's branch: the PR's review comments point at the branch's commits.
7. **Unstack.** Run `gh stack unstack`: it removes the local tracking and the GitHub grouping, not the branches or PRs.

Do not use `gh stack merge --squash`: it merges the stack into the trunk and skips the consolidated review. `gh stack` has no collapse command.

## After approval

- Land the consolidated PR yourself with `landing-a-pr` when Sami approves.
- Do not hand the approved PR back.
- Then run `post-merge`.
