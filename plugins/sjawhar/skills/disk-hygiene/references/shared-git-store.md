# Shared git store: forgetting, removing and repairing worktree entries

Read this before you forget a jj workspace, remove a git worktree, or repair one whose entry is missing. Many boxes and sessions share one git store, so a change to one entry can break another session's checkout.

- Never run a repo-wide `git worktree prune`: from a box, it deletes every other box's worktree entry. `~/.dotfiles/shims/git` refuses a bare prune.
- Drop your own entry with `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME` or `git worktree remove <path>`, so that only that one entry goes.
- If entries go missing (a git that predates the `~/.dotfiles/shims/git` prune refusal, or git run outside the shim), run `git -C <ws> rev-parse --git-dir` on each kept workspace or checkout. "is not a working tree" is the same loss.
- Before `jj workspace forget X`, run `cat X/.git` and make sure no other live worktree's `.git` names the same admin dir. If one does, the forget breaks that sibling: its git fails with `fatal: not a git repository`, while its jj keeps working, so nobody notices.
- To restore a missing entry, make `<store>/.git/worktrees/<name>/` with three files:
  - `gitdir`: absolute path of the workspace's `.git` file.
  - `commondir`: `../..`.
  - `HEAD`: output of `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`. If `@` is a non-empty described commit, run `jj new` first, but only in your own workspace; in another session's, ask that session.

  Then run `git -C <ws> read-tree HEAD`, or the worktree tracks nothing. Check `git -C <store> worktree list`, a non-zero `git -C <ws> ls-files | wc -l`, and no new sibling in `jj log -r '@ | @-'`.
- A lock, its reason string and a non-empty `@` each claim the tree is in use. Find the session that holds it (`skill://session-attribution`) and message it. Delete or overwrite the tree only after that session confirms its work is pushed or merged, and list in your report each tree you could not confirm. Do not `jj new` over another session's `@`, because that moves its work out from under it. Never force another session's lock (`-f -f`).
