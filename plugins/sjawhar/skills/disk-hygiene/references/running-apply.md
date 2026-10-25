# Running `apply` in the background, and undoing a forget

- A `while pgrep -f` loop that waits on the run needs a pattern that cannot match itself (`'disk_hygiene[.]py apply'`), or it waits forever on its own command line.
- Under `systemd-run --user`, launch with `-E PATH="$PATH"`, or `jj` is not found. If you write your own deletion loop, keep the folder when a `jj` call fails, because the forget may not have happened.
- `jj op revert <forget_op>` (from the ledger) restores a registration, not files, and does nothing if someone reused the name.
