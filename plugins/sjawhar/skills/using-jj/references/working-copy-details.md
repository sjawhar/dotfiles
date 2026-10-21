# Working-copy traps

## `jj new` comes before the work

- Snapshots put edits into whatever `@` is now. If the next work needs its own commit, run `jj new` first.
- `jj new -m "msg"` after editing makes an empty commit and leaves the work in the old change.
- `jj new main@origin` with edits does not carry them like `git checkout`. They stay in the commit you left, and the new `@` is empty.
- Recover: find the commit you left (`jj log -r 'heads(@-::) | @-'` or `jj op log`), run `jj restore --from <commit> <paths>`, then compare `jj diff -r <commit> --stat` with `jj diff --stat`. A partial restore looks complete until you compare.

## After a push, `@` is the published commit

- Further edits amend it at the next snapshot: the branch moves non-fast-forward and a sibling based on it is orphaned. Chain `jj new` with the push.
- Check for the amend: a non-zero `git merge-base --is-ancestor <pushed-sha> <new-head>`.
- Repair, when a reviewer's verdict names the pushed SHA:
  1. `jj bookmark set <name> -r <pushed-sha> --allow-backwards`. Then `jj git push -b <name> --dry-run` says `already matches`.
  2. `jj new <name>`, then `jj restore --from <amended-sha> <paths>`.
  3. `jj abandon <amended-sha>`. No bookmark points at it now, so none is deleted.
- Two wrong repairs: `jj abandon <amended-sha>` while the bookmark is on it deletes the bookmark. The next push then deletes the branch on origin, and GitHub closes the PR and may refuse to reopen it. `jj abandon --retain-bookmarks` moves the bookmark to the parent, and the push moves backward onto trunk.
- Read the `Changes to push` line before any push after a repair. `delete from` and `move backward` mean stop.

## After a rebase, check where `@` is

- `jj rebase` leaves `@` where it was. If `@` is on A and you rebase B onto A, then edit for B, the snapshot amends A, and B carries the hunk at once. `jj new` before editing does not prevent this.
- After every rebase, edit or workspace update: `jj log -r @ --no-graph -T 'change_id.short() ++ " " ++ description.first_line()'`.
- If you suspect a rewrite, grep the hunk's identifier in the file at each SHA: pre-rebase, approved tip, current tip. That shows where it entered. Diff from the SHA a gate cited, not the branch name.

## After an interruption, check what `@` is

- A grep of a stale working copy looks like a grep of main and can be wrong: `@` may be another session's refactor. Run `jj log -r '@ | main@origin'` and check they are related. `jj status` does not show this.
- Git cannot show publication: an empty `@` can have an unpublished parent. Compare `@`, `@-` and the remote bookmark with `jj log`.

## `jj describe` replaces the message

- It does not commit. It rewrites the current `@`'s message. Check `jj log -r @ --no-graph -T 'description.first_line()'` first. A `squash --into` can move `@` back onto an old commit.
- Recover: `jj op log` names the old commit ID. `jj log -r <old-id> --no-graph -T description` prints the old text.

## Merge commits

- `jj split <paths>` or `jj squash --from <merge> <paths>` on a merge deletes those files from the merged tree, because a merge's path content is its resolution. Keep the work in the merge or rebuild the delta on a fresh child.

## Colocated `HEAD`

- jj parks git `HEAD` at `@-`, so `git status` shows `@`'s content as uncommitted. That is expected. `git reset --hard`, `git checkout -- .`, `git clean` or `git stash` destroys `@`'s work past the last snapshot.
- While `@` is the bookmark commit, `git show HEAD:<file>` reads the commit before it. An added file is missing. An edited file returns the old version with exit 0. On a merge `@`, `HEAD` is only the first parent.
- `jj new`, `jj commit` and `jj squash` into the parent put the content in `@-`, where `HEAD` sees it. `jj describe` does not. Name the SHA you measured, never `HEAD`.
