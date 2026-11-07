---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM or `df` far exceeds what known work explains, on load spikes with D-state processes, when agent sessions pile up workspaces, worktrees, docker images, build cache or temp dirs, and to clean up after yourself. Triggers - disk full, ENOSPC, stale worktrees, missing git worktree entry, docker prune, buildx cache, /tmp full, io pressure, jj op store growth, a shell where every command times out.
---

# Disk hygiene

Reasons: `references/reasons.md`.

## When a shell hangs

A shell where every command times out, even `echo`, is usually not a broken shell: one stalled filesystem has wedged the session's tool threads. The search tools (`glob`, `grep`, `fuzzyFind`, `ast_grep`, `ast_edit`) and `shell.run` share ONE pool of 8 threads, and each search makes its filesystem calls inside it; an uncached inode on a stalled mount (/mnt/durable or /mnt/nvme) parks a worker in uninterruptible IO wait. At 8 of 8 parked, nothing else in the process runs - and a session is a tenant of its omp process, so one session's unbounded walk wedges every session in that process, including ones running nothing.

So the rule that matters most, more than any probe: **scope every search to a specific path, never a bare recursive walk, and never search over /mnt/durable.** Falling back to `grep` or `read` when the shell feels slow is the trap - an unbounded recursive grep parks the next eight workers.

To tell a stalled mount from a shell that cannot fork, probe in two seconds:

- `timeout 2 true` - can the shell fork at all? If this hangs, the shell itself cannot fork and the filesystem is not the cause.
- `timeout 2 ls -d <cwd>` - is that filesystem the stalled one?

If the probes hang, wait and re-probe rather than rebuild the shell or work around it; the stall recovers on its own.

## Safety rules (your own paths too)

- Never delete a directory a live session stands in.
- Never delete content that exists only on disk (untracked, gitignored and modified tracked files all count).
- Learn liveness from check-in replies and process cwds; age or mtime never proves a path dead. Use an age threshold only to keep a path longer.
- Run this skill by hand, never on a timer or cron, and stop when done. What timers already prune: `references/automatic-pruning.md`.
- A `.no-offbox` file in a directory means nothing in it or below it may leave this machine; `~/.config/no-offbox/patterns` lists more such paths. The owner of the data writes the marker (a lane on its embargoed trees), never a cleaner, and nobody removes it but that owner. A move on this machine (to `/mnt/durable`) is allowed but must keep the protection: when the marker is in a directory above the tree you move, copy it to the same directory above the new path. Run `~/.dotfiles/scripts/no-offbox <path>` before you copy anything to another machine or a bucket; any exit status but 0 means do not copy.
- **A mount's name does not tell you whether it survives a stop, and `/mnt/nvme` means opposite things on the two boxes.** Read the device before you move anything onto a mount, or treat anything found on one as safe.

  | box | mount | device | survives a stop |
  |---|---|---|---|
  | sami-devbox | `/mnt/nvme` | `/dev/nvme46n1`, model `nvme_card-pd` (GCP Persistent Disk, 16 TiB) | **yes** |
  | sami-devbox | `/mnt/scratch` | `/dev/md127`, raid0 over local SSD | no |
  | sami-rocqperf | `/mnt/durable` | `/dev/nvme0n1`, Azure managed disk `sami-rocqperf-durable-1` (Premium_LRS) | **yes** |
  | sami-rocqperf | `/mnt/nvme` | `/dev/md127`, raid0 over four `Microsoft NVMe Direct Disk v2` | no — empty after any stop |

  How to read it on an unfamiliar box, since the model string alone misleads: `findmnt -no SOURCE,FSTYPE <mount>`, then `sudo nvme list`. On Azure, a **managed** disk reports `MSFT NVMe Accelerator` with `SN: 00000`, and a **local** one reports `Microsoft NVMe Direct Disk` with a real serial; the surest check is that the managed disks share a controller with the namespace `/dev/disk/azure/os` points at, and the OS disk certainly survives a stop. `/dev/disk/azure/data/by-lun` is **empty** on a VM whose data disk arrives as an NVMe namespace rather than a SCSI LUN, so an empty `by-lun` is not evidence of no data disk — that reading cost two leads a disagreement on 2026-11-07 that took a `nvme list` to settle.

  Two lanes acted on the wrong half of this table in one morning: one was told `/mnt/durable` on rocqperf was local and nearly refused a safe move, and another raised an alarm that a lane's live tooling sat on scratch when the path was on the devbox's persistent disk all along. Name the machine with the mount, always.

Tool: `S=$SKILL/scripts/disk_hygiene.py`, `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` (`$S --help`); you judge liveness and ownership.

| Phase | Do | Gate |
|---|---|---|
| 1 | Baseline: `df`, `/proc/pressure/io`, `$S writers` and `$S cpu` while the disk still fills; pick a ledger path under `~/.local/state/` | — |
| 2 | Check in: send `references/check-in.md`'s message to each titled `envoy_sessions` session on this machine (not subagents) and build the release and protected files it describes; never skip it for being slow | Each replied or 20 min passed; protected set written |
| 3 | Inventory and classify working copies | Every non-live row has a class |
| 4 | Apply, one item at a time | Ledger shows 0 errors |
| 5 | Docker: `references/docker.md` | Each container's owner known and its verdict allows deletion |
| 6 | Caches, temp, jj op store: `references/caches.md` | Only temp-dir families `$S tmp` lists, idle >24 h |
| 7 | Report: a ledger line per manual action; a summary: space freed, what you kept and why (unpushed, holds, fresh), undescribed WIP in shared checkouts with its session or "unknown", what you could not reclaim (locks, live owners); each sampled number with its interval | Summary next to the ledger |

Largest first, the space is usually in: dangling images, buildkit cache with leaked leases, unused tagged images, orphaned sandboxes, `/tmp` families, workspaces.

## Cleaning up after yourself

Delete only paths you prove are yours; never assume an unknown workspace has no user.

- Make each work directory that must outlive your `TMPDIR` with `work-dir <name>`. Never keep work in `/tmp` on sami-rocqperf.
- Make a second tree of a repo already on the box with `agent-workspace --separate-repo` or `git worktree add` from your clone, never a full clone; remove each when its measurement or lane ends.
- In an agent box, your box's tree (your workspace's parent, minus its bind-mount sources) and `/tmp` are yours or your subagents'. Any other path is yours only if your or your subagents' transcripts (`skill://session-attribution`) show the command or write that made it; else leave it and report it.
- In your own box, also run `$S containers` and `$S images`; a `registry` container there is yours.
- Before you delete a core file, read `references/core-dumps.md`.
- Never glob a shared directory or your box tree (read `references/box-mounts.md` first): name each entry, or delete a directory you made.
- Give every `find ... -delete` or `-exec rm` a `-mindepth 1`.
- Delete a root container's files on its bind mount from a throwaway container on that mount (`references/docker.md`), then `ls` the path. Run a long delete under `systemd-run --user --wait`.
- Remove a worktree with `git worktree remove`, never by deleting its directory.

## Pacing (every phase)

- Run one `rm` or ad-hoc `du` at a time, under `sudo ionice -c3 nice -n19`.
- Delete only while `/proc/pressure/io` `full avg10 < 20`.
- Never `lsof +D` or `fuser` a tree per candidate; before each item, check one `$S procs` for a cwd in it.
- Never size a candidate to decide its deletion. Measure the result by the removed directory, not `df`; where you read `df`, read `df <path>`.
- Never run one `du` of everything, even bounded by `-d` or `-t`. Size suspected paths one at a time, `du -xsm` under `systemd-run --user -p Nice=19 -p IOWeight=10`. Where space went, what writes now, what appeared recently: `references/finding-space.md`.
- Rank space owners by each session's `du -xsm` of its own paths, never by walking all trees or by a write sample; a process, by sizing its open files twice, minutes apart.
- A free-space guard samples during a batch too: a higher number to start, a lower one to abort what runs.
- When one lead's agents share a disk, one of them reads free space and refuses for all. Across leads, run `disk-gate reserve <gib> <name>` before the work, sized by its measured peak, never by what it keeps (measure an unmeasured build in a pool pod first), and `disk-gate release <name>` after. The gate counts only the part of a reservation not yet written: it measures what your own processes write on that mount, so run the work from the shell or agent that reserved. When the work runs elsewhere (a docker build, a detached job), use `disk-gate attach <name> <pid>` or `disk-gate renew <name> --written <gib>`.
- Before you promise space back, measure every path that may share inodes (a workspace and `~/.cache/uv`, a cache and a data directory, `os.link` copies) in one `du` (`$S sizes --hardlinks <tree> <cache>`).
- When a leaked test or process causes space or load, read `references/test-leaks.md`.

## Phase 3: Classify

```bash
python3 $S inventory --repo ~/REPO --root ~/.worktrees --root /tmp > inv.json
python3 $S plan --inventory inv.json --protected protected.json [--releases releases.json] > plan.json
```

| Class | Meaning | Action |
|---|---|---|
| LIVE | a process cwd is inside | never |
| UNPUSHED | a non-empty commit on no remote bookmark | keep, list for owner |
| PUSHED | all non-empty commits on remote bookmarks | forget + rm |
| MERGED | all non-empty ancestors in trunk, `@` empty | forget + rm |
| UNREGISTERED | jj forgot it, directory remains | apply refuses |
| STALE_REGISTRATION | registered, no directory | forget if `NAME@` is merged and the jj op that registered it is older than `--fresh-hours` |
| UNOBSERVABLE | outside what this process sees | keep, never planned |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | keep unless the owner releases it |

- In a box, judge a missing directory and a process's age from a host session (`inventory --box-dir none`), not `systemd-run --user`.
- Judge by the classes, never by `jj workspace list` "(empty)".
- The freshness gate (`--fresh-hours`, default 24) never proves nothing writes in a tree; `$S procs` and `$S writers` do.
- Before you call a stale checkout's diff a revert, run `jj log -r '::@ ~ ::trunk()'`.
- Leave alone knives fork checkouts (`knives finish`, never `rm`), Legion daemon state (`~/.local/state/legion/`) and check-in holds (paths a human said to keep).
- Never run a repo-wide `git worktree prune`. Before you forget a jj workspace, remove a git worktree, repair a worktree entry, or run `jj new` in a workspace you did not create, read `references/shared-git-store.md`.

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
```

- Right before a manual `rm`, check liveness again (`$S procs`, late check-in replies).
- Before a manual `rm` of a folder holding a git store, read apply's `borrower-scan` ledger line and the store's `agent-workspace-borrowers` file.
- Before you delete a finished session's scratch, search running sessions' transcripts (`skill://session-attribution`) for the path; keep it if a task prompt names it.
- Before you delete a whole folder, `tar` all its entries next to the ledger, not only the workspaces; for a jj-only workspace, also the tree `.jj/working_copy/tree_state` records.
- Run apply under `systemd-run --user`. Before you launch it or undo a forget, read `references/running-apply.md`.
