# Working-copy traps

## `jj new` comes before the work

- A snapshot puts edits into whatever `@` is now. `jj new -m "msg"` after editing makes an empty change and leaves the work in the old one.
- `jj new main@origin` does not carry edits the way `git checkout` does: they stay in the commit you left.
- Recover: `jj log -r 'at_operation(@-, @)'` shows the commit you left (or find it in `jj op log`). Run `jj restore --from <commit> <paths>`, then compare `jj diff -r <commit> --stat` with `jj diff --stat`: a partial restore looks complete until you compare.

## After a push, `@` is the published commit

- Further edits amend it at the next snapshot: the branch then moves sideways and a branch based on it is orphaned. Chain `jj new` with the push.
- An amend shows as a non-zero exit of `git merge-base --is-ancestor <pushed-sha> <new-head>`.
- Repair, when a review names the pushed SHA:
  1. `jj bookmark set <name> -r <pushed-sha> --allow-backwards`. `jj git push -b <name> --dry-run` then reports nothing to change.
  2. `jj new <name>`, then `jj restore --from <amended-sha> <paths>`.
  3. `jj abandon <amended-sha>`. No bookmark points at it now, so none is deleted.
- Two wrong repairs: `jj abandon <amended-sha>` while the bookmark is on it deletes the bookmark, and the next push deletes the branch and closes the PR. `jj abandon --retain-bookmarks` moves the bookmark to the parent, and the push moves it backward onto trunk. Stop if a dry-run says `delete from` or `move backward`.

## After a rebase or an interruption

- `jj rebase` leaves `@` where it was. With `@` on A, rebasing B onto A and then editing for B amends A. Run `jj edit B`, or `jj new B`, first.
- Check `@` after every rebase, edit or workspace update: `jj log -r @ --no-graph -T 'change_id.short() ++ " " ++ description.first_line()'`.
- After an interruption `@` may be another session's commit, so a grep of the working copy is not a grep of main. Run `jj log -r '@ | main@origin'` and check they are related; `jj status` does not show it. An empty `@` can have an unpublished parent.
- To find where a hunk entered, grep its identifier in the file at each SHA: pre-rebase, approved tip, current tip. Diff from the SHA a reviewer or CI check cited, not from the branch name.

## `jj describe` replaced a message

- `jj op log` names the old commit ID, and `jj log -r <old-id> --no-graph -T description` prints the old text. A `squash --into` can leave `@` on an old commit, so read `@`'s message before you describe.

## Colocated `HEAD`

- jj keeps git's `HEAD` at `@-`, so `git status` shows `@`'s content as uncommitted. `git reset --hard`, `git checkout -- .`, `git clean` or `git stash` destroys `@`'s work past the last snapshot.
- `git show HEAD:<file>` reads `@-`. While `@` holds the work you mean, it misses an added file and returns the old version of an edited one, with exit 0. On a merge `@`, `HEAD` is only the first parent. Name the SHA you measured, not `HEAD`.
