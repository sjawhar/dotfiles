# Stacks, conflicts and divergence

## Publishing from a stack other sessions share

- Publish your line with `jj duplicate <commit> -o main@origin` and open the PR from the duplicate. It has no descendants in the stack, so later rewrites of the originals never move the PR head.
- Not where a fork release pins the change ID: the duplicate has a new one.

## Conflict chains

- When a rebase across a moved trunk re-conflicts each commit on the same hunks, squash your branch to one commit, then rebase and resolve once. Keep a series of commits only when the layers help review: a reviewer asked for them, or each layer is one complete change whose commit builds and passes the tests without the layers above it.
- The members of an octopus merge share one fork point: rebase each from it, not from the member's own base.
- Do not restructure a shared stack (duplicate instead) or a fork branch whose release pin follows the change ID.

## Resolving conflicts

1. Run `jj log` to see what diverged.
2. List what each side adds. After you resolve, check that each item is still in the result. Delete a local change only after its owner says yes.
3. When one side is the old version and the other the new version of the same logic, keep only the new. Then scan for duplicated blocks.
4. Compare the diff against main with the pre-rebase diff, so nothing was lost or reverted.
5. Run tests, lint and format before you squash commits together.

The `resolve-conflicts` skill has the rest.

## After a squash merge

- GitHub's squash commit has none of the branch's commits as parents, and jj does no patch-id matching. A `rebase -b` or `-s` of a stacked branch onto `main@origin` replays the merged commits: empty where main left the file alone (`--skip-emptied` drops those), conflicts where main changed it.
- Check it first: `jj log -r '<their-commit> & ::main@origin'` is empty, and `git merge-tree` of the old head against main names the conflicting files.
- Rebase only your own commits: `jj rebase -r <your commit IDs> -o main@origin`. Leave the merged ones on the old base.
- The merged PR's head branch is deleted, so `<base-branch>@origin` stops resolving. Each stacked branch's base is the commit it forked from, not the base branch's last head: derive one rebase per branch from the remote.

## Moving a child stack onto a rewritten parent

- Compute the source; do not read it off `jj log`, where short change IDs overlap across a fork. A start too late merges against a stale base and conflicts every file.
- Use `jj rebase -s 'roots(<commit the stack sits on>..<child>)' -o <new-parent>`. When the parent was rewritten elsewhere and fetched, `roots(::<child> ~ ::<new-parent>)` selects the old parent and conflicts the chain. When it was rewritten in this repository, jj already rebased the children.
- Before and after, `jj diff --name-only --from 'heads(::<child> & ::<parent>)' --to <child>` lists the same files, and `jj log -r '(::<child> ~ ::<parent>) & conflicts()'` is empty.

## Divergence

1. List the copies: `jj log -r 'change_id(<id>)'`. Inspect each by commit ID: content, bookmarks, workspace.
2. Find the copy current work uses. An operation-log snapshot can predate later edits, and a recovery copy can hold work that looks unsaved.
3. Check `descendants(<commit>) ~ <commit>` before any rewrite or abandon, and the real remote head before you change a published bookmark.
4. Point your conflicted bookmark at the verified survivor. Do not delete the remote branch to tidy the local view.
5. Abandon a stale copy only when it is your own change, its content is reachable elsewhere, `jj log -r 'descendants(<id>) ~ <id>'` is empty, and `jj log -r <id> --no-graph -T bookmarks` prints nothing. Being empty does not make a copy yours: an empty commit can be another workspace's `@`. Identify it by workspace name, and never abandon or forget a live workspace.
6. An abandon can delete local bookmarks, and the next push then deletes the remote branch: read the dry-run's direction before each push.
