# Running `apply` in the background, and undoing a forget

Read this before you launch `apply`, before a manual `rm` of a folder that holds a git store, before you write the report, or to undo a forget.

- A `while pgrep -f` loop that waits on the run needs a pattern that cannot match itself (`'disk_hygiene[.]py apply'`), or it waits forever on its own command line.
- Under `systemd-run --user`, launch with `-E PATH="$PATH"`, or `jj` is not found. If you write your own deletion loop, keep the folder when a `jj` call fails, because the forget may not have happened.
- `jj op revert <forget_op>` (from the ledger) restores a registration, not files, and does nothing if someone reused the name.
- Delete only while `/proc/pressure/io` `full avg10 < 20`. The script gates on this.
- Apply runs once per store (a second one logs `locked`) and checks each item again before it acts. Right before a manual `rm`, check liveness again (`$S procs`, late check-in replies).
- Apply refuses to delete a folder another clone borrows objects from through `objects/info/alternates`, and the ledger names the clone. It finds those clones two ways, and its `borrower-scan` ledger line says what each found. One is the `agent-workspace-borrowers` record each source keeps. `~/.dotfiles/scripts/agent-workspace --separate-repo` writes a line there for each `git clone --shared` it makes, wherever that clone lives. The other is a walk of the places this box keeps a checkout, and it stops at each checkout root. Neither sees a clone inside another checkout's working tree that no record names. Before a manual `rm` of a folder that holds a git store, read that ledger line and the store's `agent-workspace-borrowers` file.
- Run apply under `systemd-run --user`, because it can take hours and a tool timeout kills a foreground run. Launch notes, and how to undo a forget: this file.

## The ledger and the report

Write a summary next to the ledger (apply writes one JSONL line per action; add a line for each manual action). Each apply line carries the used space of that path's own filesystem, and names its mount point. The summary gives space freed, what you kept and why (unpushed, holds, fresh), undescribed WIP in shared checkouts with the session `skill://session-attribution` finds for it (or "unknown"), and what you could not reclaim (locks, live owners). Give every sampled number its interval, because a watcher reports the extreme of its samples and not the extreme.
