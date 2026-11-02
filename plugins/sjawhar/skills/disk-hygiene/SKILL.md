---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM or `df` is far above what known work explains, on load spikes with D-state processes, when agent sessions pile up workspaces, worktrees, docker images, build cache or temp dirs, and to clean up after yourself. Triggers - disk full, ENOSPC, stale worktrees, missing git worktree entry, docker prune, buildx cache, /tmp full, io pressure, jj op store growth.
---

# Disk Hygiene on a Shared Agent Box

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Reasons: `references/reasons.md`.

## Safety rules (your own paths too)

- Never delete a directory a live session stands in.
- Never delete content that exists only on disk (untracked, gitignored and modified tracked files all count).
- Learn liveness from check-in replies and process cwds; age or mtime never proves a path dead. Use an age threshold only to keep a path longer.
- Run this skill by hand, never on a timer or cron, and stop when done. What timers already prune: `references/automatic-pruning.md`.

Tool: `S=$SKILL/scripts/disk_hygiene.py`, `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` (`$S --help`); you judge liveness and ownership.

| Phase | Do | Gate |
|---|---|---|
| 1 | Baseline: `df`, `/proc/pressure/io`, `$S writers` and `$S cpu` while the disk still fills; pick a ledger path under `~/.local/state/` | — |
| 2 | Check in: send `references/check-in.md`'s message to each titled session on this machine in `envoy_sessions` (not subagents) and build the release and protected files it describes; never skip it for being slow | Each replied or 20 min passed; protected set written |
| 3 | Inventory and classify working copies | Every non-live row has a class |
| 4 | Apply, one item at a time | Ledger shows 0 errors |
| 5 | Docker: `references/docker.md` | Each container's owner known and its verdict allows deletion |
| 6 | Caches, temp, jj op store: `references/caches.md` | Only temp-dir families `$S tmp` lists, idle >24 h |
| 7 | Report: a ledger line per manual action; a summary: space freed, what you kept and why (unpushed, holds, fresh), undescribed WIP in shared checkouts with its session (`skill://session-attribution`) or "unknown", what you could not reclaim (locks, live owners); each sampled number with its interval | Summary next to the ledger |

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
- Never run one `du` of everything, even bounded by `-d` or `-t`. Size suspected paths one at a time, `du -xsm` under `systemd-run --user -p Nice=19 -p IOWeight=10`. For where space went, what writes now or what appeared recently: `references/finding-space.md`.
- Rank space owners by each session's `du -xsm` of its own paths, never by walking all trees or by a write sample; a process, by sizing its open files twice, minutes apart.
- A free-space guard samples during a batch too: a higher number to start, a lower one to abort what runs.
- When many agents of one lead write to one disk, one reads free space and refuses for all. Across leads, run `disk-gate reserve <gib> <name>` before the work and `disk-gate release <name>` after, sized by a measured peak and never by what the work keeps; measure an unmeasured build in a pool pod first.
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
- The freshness gate (`--fresh-hours`, default 24) never proves nothing writes in a tree; `$S procs` and `$S writers` do. The plan lists refusals in `refused`.
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
