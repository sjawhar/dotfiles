# Shared git store: forgetting, removing, repairing and deciding orphans

Read this before you forget a jj workspace, remove a git worktree, repair a worktree entry, delete an orphaned checkout, or run `jj new` in a workspace you did not create. Many boxes and sessions share one git store, so a change to one entry can break another session's checkout.

## Your own entry

- Never run a repo-wide `git worktree prune`: from a box, every other box's worktree looks missing, so it deletes their entries. `~/.dotfiles/shims/git` refuses a bare prune in an agent session.
- Drop only your own entry: `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME`, or `git worktree remove <path>`.
- Remove a worktree with `git worktree remove`, never by deleting its directory. `shims/git` locks every worktree it adds, and the lock outlives the directory, so a deleted directory leaves a locked entry that nothing cleans.
- Before `jj workspace forget X`, run `cat X/.git` and check that no other live worktree's `.git` names the same admin dir. If one does, the forget breaks that sibling's git (`fatal: not a git repository`) while its jj keeps working.

## Missing entries

- If entries go missing (git run outside the shim, or an older git), run `git -C <ws> rev-parse --git-dir` on each kept workspace or checkout; "is not a working tree" is the same loss.
- `git worktree repair` cannot restore an entry that was deleted, which is what a prune leaves: with `.git/worktrees/<name>` gone it has nothing to relink.
- To restore a missing entry, make `<store>/.git/worktrees/<name>/` with three files:
  - `gitdir`: absolute path of the workspace's `.git` file.
  - `commondir`: `../..`.
  - `HEAD`: the output of `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`. If `@` is a non-empty described commit, run `jj new` first, but only in your own workspace; in another session's, ask that session.

  Then run `git -C <ws> read-tree HEAD`, or the worktree tracks nothing. Check `git -C <store> worktree list`, a non-zero `git -C <ws> ls-files | wc -l`, and no new sibling in `jj log -r '@ | @-'`.

## Someone else's tree

- A lock, its reason string and a non-empty `@` each claim the tree is in use. Find the session that holds it (the `session-attribution` skill) and message it. Delete or overwrite the tree only after that session confirms its work is pushed or merged; list each tree you could not confirm in your report.
- Never `jj new` over another session's `@` (it moves their work out from under them), and never force another session's lock (`-f -f`).

## Orphaned checkouts

Orphans (a checkout whose entry is gone or reused) are common here. Age is a first sort only: an orphan older than every live worktree is still not proof.

- An orphan whose entry name was reused by another worktree: remove it with `rm -rf`, never `git worktree remove`. `git worktree remove <orphan path>` refuses with `is not a working tree`, which is not a clean bill of health; `git worktree remove <bare NAME>` exits 0 and deletes the live worktree that now holds that name, directory included.
- Before you remove anything that looks abandoned, read `<orphan>/.git` for its admin directory, then that directory's `gitdir` file. If it does not name the orphan's path, the entry belongs to another tree now.
- Inside such an orphan, `git status`, `git log` and `git diff` answer about the other tree. Compare files instead: `diff -rq <orphan> <live> -x .git -x __pycache__ -x .venv -x node_modules`. An empty result means the orphan holds nothing the live tree lacks. With no live counterpart, `tar` the whole orphan into a file next to the ledger, and check that `tar -tf <file>` lists its files, before you remove it.
- An orphan whose entry is gone: read its tree into a throwaway index, which touches nothing shared.

  ```bash
  i=$(mktemp -u); g="git --git-dir=<repo>/.git --work-tree=<orphan>"
  GIT_INDEX_FILE=$i $g read-tree <commit>
  GIT_INDEX_FILE=$i $g update-index -q --refresh
  GIT_INDEX_FILE=$i $g diff-files --name-only
  GIT_INDEX_FILE=$i $g ls-files --others --exclude-standard
  ```

  Set `GIT_INDEX_FILE` per command, never `export` it: the agent shell persists.
  - **Why:** every later git command in every repository would use an exported `GIT_INDEX_FILE`. `git status` would show nearly every file as a staged deletion, and a commit would commit those deletions.

  To clean a shell where it was exported: `unset GIT_INDEX_FILE`, then `git log --stat` over the session's commits and look for a file count far larger than the change.
- `<commit>` must be the commit that tree was at: find it by the worktree's own name (`git for-each-ref | grep <name>`), then confirm it with `git ls-remote`. Against any other base, a clean tree reads as thousands of changes. Both lists empty and `<commit>` on a remote means the orphan holds nothing unsaved: remove it and write one ledger line. A non-empty result never allows a delete, and it shows unsaved work only against the tree's own commit.

## Stray `.git` directories

Never run `git init` in a shared root such as `/tmp`, `/var/tmp`, `/mnt/nvme/<user>/tmp` or a session-tmp root: git looks upward for a repository, so a stray `.git` captures every git command run below it in a directory without its own (`git apply` then skips every patch and exits 0). A script that runs git in a temp directory sets `GIT_CEILING_DIRECTORIES="$(dirname "$PWD")"` and checks `git rev-parse --git-dir`. Rename an untraced stray `.git` aside and record it; do not delete it.
