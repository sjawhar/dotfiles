---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, `df` exceeds what known work explains, IO pressure or D-state processes hang shells, a git worktree entry goes missing, the jj op store grows, or sessions leave workspaces, worktrees, docker images, build cache or temp dirs behind, your own included.
---

# Disk hygiene

The script does the mechanical part; you decide what is live and who owns it.

**Done when:** you reported next to the ledger, with a ledger line for each manual action: space freed, by `du` of the removed paths (other sessions move `df`); what you kept and why (unpushed, holds, fresh); undescribed work in shared checkouts with its session or "unknown"; what you could not reclaim (locks, live owners). Give each sampled number its interval.

Set `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` and `S=$SKILL/scripts/disk_hygiene.py` (the skill URL does not resolve in bash). `python3 $S --help` lists the subcommands and the protected and releases file formats.

## When a shell hangs

Every command timing out, even `echo`, usually means a stalled filesystem. omp's search tools and its shell share one small thread pool. Searches on a stalled mount block it for every session in that omp process. Switching to `grep` or `read` blocks more threads.

- Scope every search to a named path.
- Never run a bare recursive walk.
- Never search over `/mnt/durable`.
- Probe with `timeout 2 true`: it hangs when the shell cannot fork (not the filesystem).
- Probe with `timeout 2 ls -d <cwd>`: it hangs when that filesystem is stalled.
- Wait and probe again: the stall recovers by itself.

## Safety rules (your own paths too)

- Never delete a directory a live session stands in.
- Never delete content that exists only on disk (untracked, gitignored and modified tracked files all count).
- Learn liveness from check-in replies and process cwds (`$S procs`).
- Age or mtime never proves a path dead.
- An age threshold may only keep a path longer.
- Cold is not finished: such a worktree can be withdrawn work (a review checkout of a squash-merged pull request) or the only copy. Only its owner or the repository's own record settles it.
- Not on a remote is not unsaved, for the same reason. Only the worktree's owner or the repository's own record settles it.
- A dangling `.git` does not prove abandonment: any session's `git worktree prune` drops live registrations.
- `[ -e .git ]` tests the file, not a registration.
- Run `chmod -R u+w <tree>` before an `rm -rf`: `rm -rf` leaves read-only trees partly deleted, and the rest looks like someone else's files.
  - After the `rm -rf`, check that the path is gone.
  - Use sudo only on the owner's say-so.
- Run this skill by hand, never on a timer (it needs the check-in).
- Stop this skill when done.
- A `.no-offbox` file in a directory, or a match in `~/.config/no-offbox/patterns`, means nothing in or below it may leave this machine.
- Only the data's owner writes or removes the marker.
- A move on this machine keeps the marker: a marker inside the moved tree moves with it.
- For a move on this machine, copy a marker N levels above the tree to the directory N levels above the new path.
- Before you copy anything off the machine, run `~/.dotfiles/scripts/no-offbox <path>`; a nonzero exit means do not copy.
- Read `references/box-mounts.md` before you move data onto a mount, count on data on a mount to survive a machine stop, choose where temp output goes in an agent box, or delete inside a box's own tree.

## Procedure

1. **Baseline:** `df <path>`, `/proc/pressure/io`, `sudo python3 $S writers` and `python3 $S cpu` while the disk still fills.
   - Pick a ledger path under `~/.local/state/`.
   - Read `references/finding-space.md` before you size, rank or watch disk use.
2. **Check in:** read `references/check-in.md`.
   - Send its message to each titled session `envoy_sessions` lists on this machine (not subagents).
   - Build the protected and releases files.
   - Go on when each session has replied or 20 minutes have passed.
   - Replies change the plan.
3. **Classify:** read `references/workspaces.md`, then classify:
   ```bash
   python3 $S inventory --repo ~/REPO --root ~/.worktrees --root /tmp > inv.json
   python3 $S plan --inventory inv.json --protected protected.json [--releases releases.json] > plan.json
   ```
4. **Apply:** apply until the ledger shows 0 errors:
   ```bash
   systemd-run --user --same-dir -E PATH="$PATH" nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
   ```
   - To undo a forget, read `references/workspaces.md`.
5. **Docker:** follow `references/docker.md`.
6. **Temp, caches and the jj op store:** follow `references/caches.md`.

Read `references/shared-git-store.md` before you prune, forget a jj workspace, remove a git worktree, repair a worktree entry, delete an orphaned checkout, or run `jj new` in a workspace you did not create.

The space is usually, largest first, in: dangling images, buildkit cache with leaked leases, unused tagged images, orphaned sandboxes, `/tmp` families, workspaces.

- `~/.dotfiles/devbox/box-cleanup` (its cleaners: `devbox/box-cleanup.md`) and `~/.dotfiles/agentbox/devbox-prune` (its header) already prune dangling Docker objects, idle Go build cache, capped tool caches and stale `/tmp` entries.
- Read `box-cleanup` and `devbox-prune` before you add automation.
- In a box, `systemctl --user status devbox-prune.service` shows the host unit's last run.
- The box's `journalctl` cannot read the host unit's log: read it from a host session, `journalctl --user -u devbox-prune.service`.
- Nothing automatic judges a workspace or a tagged image live.
- Do by hand what `box-cleanup` and `devbox-prune` leave: unused tagged images, non-default builders, leaked leases, containers, Docker volumes, box directories agentbox did not delete, `~/.local/share/opencode` and a retired jj store (`references/caches.md` defines it).

## Pacing (every phase)

- Run one `rm` or ad-hoc `du` at a time: parallel walkers plus an `rm` can hang every shell.
- Run each `rm` or ad-hoc `du` under `sudo ionice -c3 nice -n19`, for the same reason.
- Delete only while `/proc/pressure/io` `full avg10` is under 20 (`apply` and `tmp --apply` wait on it: `--io-limit`).
- Never `lsof +D` or `fuser` a tree per candidate.
- Before each candidate, check one `$S procs` for a cwd inside it.
- Never size a candidate to decide its deletion.
- Never run one `du` of everything.
- Read `references/shared-disk-writes.md` before a step that writes more than 2 GB to a shared mount, or before you write a free-space guard for a batch.
- Before you promise space back, measure every path that may share inodes in one run: `$S sizes --hardlinks <tree> <cache>` (a workspace and `~/.cache/uv`, a cache and a data directory, `os.link` copies).
- Read `references/test-leaks.md` when processes a test or harness left behind cause the space or load.

## Cleaning up after yourself

Delete only paths you prove are yours; a name proves nothing. Read `references/own-paths.md` when you clean up after yourself, and before you make a work directory, keep work in `/tmp`, or make a second tree of a repository.

- Read `references/finding-space.md` before you delete by a glob or with `find`.
- Run a delete under `systemd-run --user --wait` when you cannot show that it ends inside your tool call's timeout, so the timeout does not stop it halfway.
