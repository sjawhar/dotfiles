# Stacks, conflicts and divergence

## Publishing from a shared stack

- When several sessions' commits sit in one local stack, publish each line with `jj duplicate <commit> -d main@origin` and open the PR from the duplicate. It has no descendants in the stack, so later rewrites of the originals never move the PR head.
- Never do this where a release pin must follow the change ID.

## Conflict chains

- When a rebase across a moved trunk re-conflicts each commit on the same hunks, do not grind through it. Squash the branch to one commit, or the few the reviewer needs. Then rebase and resolve once.
- The members of an octopus merge share one fork point.
- Exceptions: a shared stack you cannot restructure (duplicate instead) and a fork branch whose release pin follows the change ID.

## Resolving conflicts

1. Run `jj log` to see what diverged.
2. Review what changed in each side. Never lose functionality, and never delete local changes without permission.
3. When one side is the old version and the other the new version of the same logic, keep only the new. Then scan for duplicated blocks.
4. Compare the diff against main with the pre-rebase diff to check nothing was lost or reverted.
5. Run tests, lint and format before you squash commits together.

## After a squash merge

- GitHub's squash commit has none of the branch's commits as parents, and jj does no patch-ID matching. A `rebase -b`/`-s` of a stacked branch onto `main@origin` replays them per file. Where main left the file alone, they are empty commits (`--skip-emptied` drops them). Where main changed it, they are conflicts.
- Prove it: `jj log -r '<their-commit> & ::main@origin'` is empty. `git merge-tree` of the old head against main names the conflicting file before any rebase.
- Fix: `jj rebase -r <your own commit ids> -d main@origin`. Leave the merged commits on the old base.
- The merged PR's head branch is deleted, so `<base-branch>@origin` stops resolving.
- Each stacked branch's base is the commit it forked from, not the base branch's last head. Derive one rebase per branch from the remote.

## Moving a child stack onto a rewritten parent

- Compute `-s`. Never read it off `jj log`, where short change IDs overlap across a fork. A start too late merges against a stale base and conflicts every file.
- Use `jj rebase -s 'roots(<commit the stack sits on>..<child>)' -d <new-parent>`. When the parent was rewritten elsewhere and fetched, `roots(::<child> ~ ::<new-parent>)` selects the old parent and conflicts the chain. When it was rewritten in this repository, jj already rebased the children.
- Before and after, check that `jj diff --name-only --from 'heads(::<child> & ::<parent>)' -r <child>` lists the same files and `(::<child> ~ ::<parent>) & conflicts()` is empty.

## Divergence

1. List the copies: `jj log -r 'change_id(<id>)'`. Inspect each by commit ID: content, bookmarks, workspace owner.
2. Find the copy the current work uses. An operation-log snapshot can predate later edits. A recovery copy can hold work that looks unsaved.
3. Check `descendants(<commit>) ~ <commit>` before any rewrite or abandon.
4. Check the real remote head before you change a published bookmark.

- Point an owned conflicted bookmark at the verified survivor. Do not delete the remote branch to tidy the local view.
- Abandon a stale copy only when it is your own change, its content stays reachable, `jj log -r 'descendants(<id>) ~ <id>'` is empty and `jj log -r <id> --no-graph -T bookmarks` prints 0 bytes. Empty is not an ownership test.
- A recreated empty commit can be another workspace's `@`. Identify it by workspace name. Never abandon or forget a live workspace.
- An abandon can delete local bookmarks and rebase other work, and a later push can then delete the remote branch. Read the dry-run direction before each push.
