# Phase 6: temp, caches and the jj op store

Read this in Phase 6, before you delete temp directories, caches or jj operations. `S` and `SKILL` are set in SKILL.md.

- `python3 $S tmp --older-than-hours 24 --protected protected.json` lists stale temp families, the names matching `--families`. `--dir` defaults to `/tmp`. It dates each by the newest mtime inside it, not the directory's own.
- Add `--apply --ledger <ledger>` to that `tmp` command to delete.
- Ask Sami before you delete a `/tmp` entry no family matches: nothing shows whose it is.
- Never delete by a glob in a shared directory (`finding-space.md`): the glob matches other owners' files too.
- Run `uv cache prune` when no `uv` process runs, with `UV_LOCK_TIMEOUT=3600` so it waits for the cache lock. Never `--force`: it ignores the lock running `uv` processes hold.
- Per-commit caches (`~/.cache/<tool>/<sha>`): keep each sha a remaining checkout's lockfile names; delete the rest.
- `atime` cannot say whether a cache is in use: every walk sets it (a `du`, a `find`, this skill's inventory), and `relatime` updates it at most daily. Judge use by live processes (`$S procs`, `$S writers`) and the lockfiles that name each sha.
- Before you blame workspaces, check `.jj/repo/op_store` (`ls .jj/repo/op_store/views | wc -l`, per-view size). A stray `refs/pull/*` fetch adds `<n>@pr` bookmarks to each view; `jj git remote remove pr` drops them.
- Compact an op store with `python3 $SKILL/scripts/jj_opstore_gc.py --repo R --keep-days N --apply`, never plain `jj op abandon`, which strands workspaces. It abandons every operation older than N days. Without `--apply` it changes nothing.
- Set N in `--keep-days N` to 3, the script's default, unless someone on the store you compact still needs an older operation (to restore or revert it); then set N above that operation's age in days.
- Run `jj_opstore_gc.py` when no jj command runs on the store it compacts: a concurrent commit can make a change divergent.
- On a shared store, ask Sami before you compact its op store: the abandon cannot be undone.
- A retired jj store is a repository checkout whose jj store (`.jj/repo`) a newer clone of the same repository replaced, so sessions add no workspaces to it. Remove one by hand only when `jj -R <checkout> workspace list` lists no workspace but `default` and `$S procs` shows no process cwd inside it; SKILL.md's safety rules still apply to its `default` workspace.
