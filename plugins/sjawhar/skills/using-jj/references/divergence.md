# Resolve divergent changes without moving someone else's work

A change ID can name several commit IDs after a rewrite. That is bookkeeping,
not proof of corruption or a reason to delete remote history.

## Inspect before cleanup

1. List the siblings with `jj log -r 'change_id(<id>)'` and inspect each by its
   unambiguous commit ID. Record their contents, bookmarks and workspace owners.
2. Identify the version the current work actually uses. An operation-log snapshot
   can predate later edits; a recovery sibling may contain unsaved-looking work.
3. Inspect `descendants(<commit>) ~ <commit>` before any rewrite or abandon.
   Both `-r` and `-s` rebases can move other owners' descendants, by different rules.
4. Check the actual remote head before changing a published bookmark. Local state
   does not prove what a reviewer or another workspace has fetched.

## Select the narrow remedy

- An ambiguous change ID needs an explicit commit ID for a one-revision command.
  After a rewrite, find the surviving commit again; the old ID still names the old snapshot.
- An owned conflicted bookmark can be pointed at the verified surviving commit.
  Do not delete the remote branch to tidy the local view.
- Abandon a superseded copy only when it is yours, its contents remain reachable,
  no bookmark depends on it and it has no descendants. Empty is not an ownership test.
- A repeatedly recreated empty commit may be another workspace's current `@`.
  Identify it by workspace name; do not abandon or forget another live workspace.
- An immutable refusal requires finding the pin, for example
  `jj log -r 'immutable_heads() & descendants(<commit>)'`. Do not override it to
  clean up a published or shared revision. For a managed fork, its release workflow
  owns the pin and stable change IDs; use `fork-work` and `using-knives`.

An abandon pushes nothing by itself, but it can delete local bookmarks and rebase
other work, and a later named push can then delete the remote branch. Empty
leftovers are not automatically safe to abandon. Inspect the dry-run direction
before every publication and verify the remote bookmark still names the intended
commit afterward.

No repository-global `jj undo`, `jj op restore` or `jj op revert` is a safe repair
in a shared store. Recover with a narrowly scoped forward change instead.
