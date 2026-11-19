# Phases 3 and 4: classify and apply working copies

Read this before you run `inventory`, `plan` or `apply`, or before a manual `rm` of a working copy. `S` is set in SKILL.md.

## What each class means for you

- `LIVE`: a process stands in it. Leave it.
- `UNPUSHED`, `GIT_UNPUSHED`: it holds commits no remote has. Keep it and list it for its owner.
- `PUSHED`, `MERGED`, `STALE_REGISTRATION`: `apply` handles it. Nothing to do by hand.
- `SCRATCH`: a plain directory `apply` removes once idle, unless the protected set covers it.
- `HOLDS_WORKING_COPY`, `SCRATCH_UNVERIFIED`: a plain directory with a checkout inside, or one the scan could not read. Keep it; look inside by hand if it is large.
- `UNREGISTERED`: jj forgot it but the directory remains, and `apply` will not remove it. Find its owner before you remove it by hand.
- `UNOBSERVABLE`: outside what this process can see, often another box's. Judge it from a host session.
- `GIT_HEAD_PUSHED`: a plain git worktree, removed only through its owner's release (`check-in.md`).

## Classify

- In a box, a missing directory proves nothing and `ps` ages read 0. Judge absence and process age from a host session (`inventory --box-dir none`); `systemd-run --user` does not leave the box.
- Judge by the classes, never by `jj workspace list` "(empty)": it says nothing about unmerged ancestors.
- Idle hours never prove that nothing writes in a tree; `$S procs` and `$S writers` do.
- A stale checkout's diff shows later merges as changes: run `jj log -r '::@ ~ ::trunk()'` before you call it a revert.
- Leave alone knives fork checkouts (`knives finish`, never `rm`), check-in holds and Legion daemon state (`check-in.md` finds its paths).

## Apply

- Keep `systemd-run` and `-E PATH="$PATH"` in SKILL.md's apply command: a tool timeout kills a foreground run, and without `PATH` `jj` is not found.
- A loop that waits on the run needs a pattern that cannot match itself: `pgrep -f 'disk_hygiene[.]py apply'`.

## A manual `rm`

- Right before it, check liveness again (`$S procs`, late check-in replies).
- For a folder that holds a git store, read apply's `borrower-scan` ledger line and the store's `.git/agent-workspace-borrowers` file first.
- Before you delete a finished session's scratch, search running sessions' transcripts (the `session-attribution` skill) for the path; keep it if a task prompt names it.
- Before you delete a whole folder, `tar` all its entries next to the ledger, not only the workspaces; for a jj-only workspace, also the files `.jj/working_copy/tree_state` records.
- In your own deletion loop, keep the folder when a `jj` call fails: the forget may not have happened.
