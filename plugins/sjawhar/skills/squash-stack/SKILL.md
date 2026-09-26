---
name: squash-stack
description: Collapse a completed gh-stack PR stack into a single PR carrying the whole diff. Use when a gh-stack stack is fully implemented and each stacked PR has been reviewed, when finishing a multi-PR plan built via the sdd command's gh-stack step, or when asked to collapse/consolidate/squash a stack down to one PR.
---

# Squash Stack

Turns a reviewed `gh-stack` stack of PRs into one PR carrying the whole diff. This is the last
step of the multi-PR path in the `sdd` command: stack, implement, open + review each PR as it
lands, smoke-test, final review — then squash-stack.

No new branch and no new PR: the stack's top PR becomes the consolidated PR.

## Preconditions

- The stack is complete and tracked (`gh stack view --json` returns it), every branch pushed,
  every PR open.
- Every PR in the stack has had its agent review and the findings are addressed. If not, fix on
  the owning branch and `gh stack rebase --upstack` first.
- The sdd command's smoke-test phase and final review pass are clean.

## Grouping has a CI cost — do not leave a stack grouped while you are still pushing to it

A PR inside a GitHub **native stack** (`gh stack link`) receives NO `github-actions` check suites on
`synchronize` or `labeled`. Measured twice on 2026-09-26: #20282 (pushes at 08:29Z and 08:59Z, label
lever at 09:07Z) and #20138 (push at 05:31Z) sat with zero suites while unrelated PRs ran normally,
and #20282's own pre-grouping push at 05:19Z HAD run. `gh stack unstack <n>` — which removes tracking
only, touching no branch and no base — brought `PR Checks queued` back within 30 seconds on both heads.
The mechanism is not known; only the behaviour is measured, so treat this as a reproducible symptom
rather than a settled cause.

So the bases carry the order, not the grouping: leave the stack UNGROUPED while it is being worked, name
each PR's base explicitly in its packet, and group only at merge time if your merge tooling needs it.
If a stack you did not create is missing CI, check for grouping before re-running anything — a re-run
cannot produce a suite that the event never created.

## Collapsing the stack

1. **Read the stack's shape** — trunk, the branches in order, the top branch and its PR:
   ```bash
   gh stack view --json
   ```
2. **Sync** so trunk and every branch are current:
   ```bash
   gh stack sync --prune
   ```
3. **Re-point the top PR at trunk.** Its base is currently the next branch down; the top branch
   already contains the whole stack's diff, so after this the PR shows the full change:
   ```bash
   gh pr edit <top-pr> --base <trunk>
   ```
4. **Squash the commits** on the top branch and push:
   ```bash
   git checkout <top-branch>
   git reset --soft $(git merge-base <trunk> HEAD)
   git commit -m "<one summary of the whole change>"
   git push --force-with-lease
   ```
   Keep a small deliberate commit series instead only when the layering genuinely helps review.
5. **Rewrite the PR title and description.** After squashing, both must describe the whole
   change, not the last stacked slice:
   ```bash
   gh pr edit <top-pr> --title "<consolidated title>" --body-file <body.md>
   ```
   The body summarizes what the whole stack built, lists the PRs it consolidates (numbers,
   links, each one's review outcome), and carries the `## Verification` section per
   `opening-a-pr`.
6. **Close the base PRs**, pointing at the consolidated one; leave their branches alone:
   ```bash
   gh pr close <N> --comment "Consolidated into #<top-pr>; the reviewed diff lives there."
   ```
7. **Retire the stack grouping** (removes tracking only; touches no branches or PRs):
   ```bash
   gh stack unstack
   ```

## After approval

When Sami approves the consolidated PR, merge it — squash-merge, no admin-merge, no bypassing
branch protection. Never hand an approved PR back to him to click. Then run the `post-merge`
skill's sweep.

## Verified against the real CLI

`gh stack` (github/gh-stack v0.1.0) has no native collapse: there is no `squash` subcommand,
and `gh stack merge --squash` squash-merges the whole stack straight into trunk in one shot,
skipping the consolidated-review step — so it is not used here. `view --json`, `sync --prune`,
`rebase --upstack`, and `unstack` were checked against `--help` on this machine; the git squash
mechanics are standard. The full sequence has not yet been run end-to-end against a live stack —
treat conflicts at step 4 as ordinary conflict resolution, not a broken sequence.
