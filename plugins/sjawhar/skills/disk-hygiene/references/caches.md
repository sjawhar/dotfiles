# Phase 6: Caches, temp and the jj op store

`S` and `SKILL` are as set in SKILL.md.

- `python3 $S tmp --older-than-hours 24 --protected protected.json` lists stale temp families (directories whose names match its `--families` regex; `--dir` defaults to `/tmp`). Add `--apply --ledger` to delete. Ask Sami before you delete a `/tmp` entry that `$S tmp` does not list in a family, because no rule shows whose it is. Never delete by glob in a shared namespace on one owner's word, because the glob also matches other owners' files.
- Run `uv cache prune` when no `uv` process runs, with `UV_LOCK_TIMEOUT=3600` so it waits for a cache lock instead of failing. Never `--force`: it ignores the cache lock that running `uv` processes hold.
- Per-commit caches (`~/.cache/<tool>/<sha>`): keep each sha that a remaining checkout's lockfile names. Delete the rest.
- Before you blame workspaces, check `.jj/repo/op_store` (`ls .jj/repo/op_store/views | wc -l`, per-view size). A stray `refs/pull/*` fetch adds `<n>@pr` bookmarks to each view. `jj git remote remove pr` drops them.
- Compact the op store with `python3 $SKILL/scripts/jj_opstore_gc.py --repo R --keep-days N --apply`, never plain `jj op abandon`, which strands workspaces. Without `--apply` it changes nothing. Run it when no jj command runs on that store: a concurrent jj commit can make a change divergent. The abandon cannot be undone, so on a shared store ask Sami to approve it first.
