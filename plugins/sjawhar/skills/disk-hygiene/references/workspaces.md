# Phases 3 and 4: classify and apply working copies

Read this before you run `inventory`, `plan` or `apply`, before a manual `rm` of a working copy or of a folder that holds a git store, or to undo a forget. `S` is set in SKILL.md.

## What each class means for you

| Class | What it is | Action |
| --- | --- | --- |
| `LIVE` | A process stands in it. | Leave it. |
| `UNPUSHED`, `GIT_UNPUSHED` | It holds commits no remote has. | Keep it and list it for its owner. |
| `PUSHED`, `MERGED`, `STALE_REGISTRATION` | | `apply` handles it. Nothing to do by hand. |
| `SCRATCH` | A plain directory. | `apply` removes it once idle, unless the protected set covers it. |
| `HOLDS_WORKING_COPY`, `SCRATCH_UNVERIFIED` | A plain directory with a checkout inside, or one the scan could not read. | Keep it. Look inside it by hand when `$S sizes --top 15` on its root lists it (`finding-space.md`). |
| `UNREGISTERED` | jj forgot it but the directory remains. | `apply` will not remove it. Find its owner before you remove it by hand. |
| `UNOBSERVABLE` | Outside what this process can see, often another box's. | `plan` never plans it: keep it, and judge it from a host session. |
| `GIT_HEAD_PUSHED` | A plain git worktree. | Removed only through its owner's release (`check-in.md`). |

## Classify

- In a box, a missing directory proves nothing and `ps` ages read 0. Judge absence and process age from a host session (`inventory --box-dir none`), not through `systemd-run --user`, which from a box starts its command on the host (`box-mounts.md`).
- Judge by the classes, never by `jj workspace list` "(empty)": it says nothing about unmerged ancestors.
- Idle hours come from the freshness gate (`plan --fresh-hours`, default 24): the mtime of `.jj/working_copy/tree_state` for a jj workspace, and the newest mtime in the top 3 levels for a plain directory. A file written deeper does not move it, so idle hours are an upper bound on how stale a tree is, never proof that nothing writes in it; `$S procs` and `$S writers` give that proof.
- A stale checkout's diff shows later merges as changes: run `jj log -r '::@ ~ ::trunk()'` before you call it a revert.
- Leave alone knives fork checkouts (each path `knives repos --json` lists, every sibling of a `default` checkout it lists, and each `workspaces` directory in `~/.config/knives/repos.toml`; end one with `knives finish`, never `rm`), check-in holds and Legion daemon state (`check-in.md` finds its paths).

## Apply

- Keep `systemd-run` and `-E PATH="$PATH"` in SKILL.md's apply command: a tool timeout kills a foreground run, and without `PATH` `jj` is not found. From a box, run that `apply` as a named bash service instead: there `systemd-run --user` runs it on the host (`box-mounts.md`).
- A loop that waits on the run needs a pattern that cannot match itself: `pgrep -f 'disk_hygiene[.]py apply'`.
- To undo a forget, run `jj op revert <forget_op>` with the `forget_op` of the forget's ledger line. It restores the registration, not the files, and does nothing if someone reused the name since.

## A manual `rm`

- Right before it, check liveness again (`$S procs`, late check-in replies).
- For a folder that holds a git store, read apply's `borrower-scan` ledger line and the store's `.git/agent-workspace-borrowers` file first.
- Before you delete a finished session's scratch, search running sessions' transcripts (the `session-attribution` skill) for the path; keep it if a task prompt names it.
- Before you delete a whole folder, `tar` all its entries next to the ledger, not only the workspaces; for a jj-only workspace, also the files `.jj/working_copy/tree_state` records.
- In your own deletion loop, keep the folder when a `jj` call fails: the forget may not have happened.
