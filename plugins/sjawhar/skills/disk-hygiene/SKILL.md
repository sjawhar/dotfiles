---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, or `df` is far above what known work explains. Also use on load spikes with D-state processes, or when workspaces, worktrees, docker images, build cache or temp dirs pile up from agent sessions. Also use to clean up after yourself. Triggers - disk full, ENOSPC, stale worktrees, missing git worktree entry, docker prune, buildx cache, /tmp full, io pressure, jj op store growth.
---

# Disk Hygiene on a Shared Agent Box

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Safety rules

- Never delete a directory that a live session stands in: its shell and tools fail at once.
- Never delete content that exists only on disk (untracked, gitignored and modified tracked files all count): nothing can bring it back.
- Learn liveness from check-in replies and process cwds, because an idle live session looks the same on disk as a dead one. Never treat age or mtime as proof that a path is dead: a rebase rewrites every working copy's mtime, and idle sessions stay live. Use an age threshold only to keep a path longer; never use age to decide that a path may be deleted.

Tool: `S=$SKILL/scripts/disk_hygiene.py`, with `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` (`skill://` does not resolve in bash, and a `find` under load can time out). `$S --help` lists subcommands and file formats. The script does the mechanical parts; you decide what is live and who owns it.

| Phase | Do | Gate |
|---|---|---|
| 1 | Baseline: `df`, `/proc/pressure/io`, `$S writers` and `$S cpu` while the disk still fills; pick a ledger path under `~/.local/state/` | — |
| 2 | Check in with every live session | Each replied or 20 min passed; protected set written |
| 3 | Inventory and classify working copies | Every non-live row has a class |
| 4 | Apply, one item at a time | Ledger shows 0 errors |
| 5 | Docker: [references/docker.md](references/docker.md) | Each container's owner known and its verdict allows deletion |
| 6 | Caches, temp, jj op store: [references/caches.md](references/caches.md) | Only temp-dir families `$S tmp` lists (directories whose names match its `--families` regex), idle >24 h |
| 7 | Report | Summary next to the ledger |

Where the space usually is, largest first: dangling images, buildkit cache with leaked leases, unused tagged images, orphaned sandboxes, `/tmp` families, workspaces. Workspaces free less than they look, because `uv` venvs hardlink into `~/.cache/uv` where the cache shares their filesystem. `$S sizes --hardlinks <tree> <cache>` measures the shared bytes: sizing the two trees in two commands counts them in both.

## Cleaning up after yourself

Here you delete only paths that you prove are yours. A name proves nothing either way, so never assume an unknown workspace has no user.

- Make each working directory that must outlive your session's `TMPDIR` with `work-dir <name>`. It makes `/mnt/nvme/<user>/tmp/<name>` with an `OWNER` file that names your session, so the tmp reaper and the box-health lead can tell it is yours (`devbox/session-scratch-reap.md`). A directory with no `OWNER` is never reaped and has no known owner after a loss (dispatch://INFRA-893). Never keep work in `/tmp` on sami-rocqperf: it is on the shared root disk, and `systemd-tmpfiles` empties it at every boot (`D /tmp` in `/usr/lib/tmpfiles.d/tmp.conf`); the reboot of 2026-10-31T22:35Z removed every entry.
- Make a second tree of a repository that is already on the box with `~/.dotfiles/scripts/agent-workspace --separate-repo` (a `git clone --shared`) or `git worktree add` from your clone, never with a second full clone, and remove each tree when its measurement or lane is done. On 2026-11-01, 16 full clones of one repository held about 38 GB of the same history on `/mnt/nvme`, and a census that added one 3.3 GB worktree every 2 minutes took the disk from 59 GiB free to 4 GiB in 12 minutes.
- In an agent box, your box's own tree (the parent of your workspace: `/mnt/nvme/<user>/agentbox/boxes/<your box>/` here, `~/boxes/<your box>/` on a machine without that disk — except the bind-mount sources below) and the box's `/tmp` are yours or your subagents'. On the host, every path needs the transcript test below.
- Any other path is yours only if your transcripts or your subagents' show the command that made it (`jj workspace add`, a clone, `mktemp`, a write). `skill://session-attribution` gives the transcript paths. If no transcript of yours names it, leave it and list it in your report.
- Your own paths must still pass the safety rules: no live process in it (`$S procs`), no content only on disk.
- In your own box, also run `$S containers` and `$S images`, and treat a `registry` container in that output as yours: your subagents may have started it without your seeing it.
- Before you delete a core file, read [references/core-dumps.md](references/core-dumps.md): it can be the only crash evidence.
- Never glob a shared directory: a prefix is not ownership. Name each entry you delete, or delete a directory you made. `rm -f /tmp/mt.*`, cleaning one agent's own pid-suffixed scratch, destroyed two unrecoverable files of somebody else's that shared the prefix. The box tree needs the same rule for a second reason, its live bind-mount sources: [references/box-mounts.md](references/box-mounts.md).
- Give every `find ... -delete` or `find ... -exec rm` a `-mindepth 1`, because `find` tests the starting directory too and has deleted the directory it was told to clean inside ([references/finding-space.md](references/finding-space.md)).
- A container that ran as root left root-owned files on its bind mount, and `rm -rf` as your user fails on each of them with `Permission denied`, per file, so the tree looks deleted at the top and is not. Remove such a tree with a throwaway container on the same mount (`docker run --rm -v <parent>:/w alpine:3 rm -rf /w/<tree>`), then `ls` the path to confirm it is gone. A long delete under a tool time limit can stop halfway: run it under `systemd-run --user --wait`.
- Remove a worktree with `git worktree remove`, never by deleting its directory. `~/.dotfiles/shims/git` locks every worktree it adds (AGENTC-431), and that lock outlives the directory, so a deleted directory leaves a locked registration in the shared store that nothing cleans and no prune may touch. Two such registrations survived from 2026-11-01 08:30Z until their owner unlocked and removed each by exact path.

## Pacing (every phase)

- Run one `rm` or ad-hoc `du` at a time, under `sudo ionice -c3 nice -n19`. Parallel walkers plus an `rm` can hang every shell on the box.
- Delete only while `/proc/pressure/io` `full avg10 < 20`. The script gates on this.
- Never `lsof +D` or `fuser` a tree per candidate: each walks the whole tree. Before each item, run `$S procs` once and check that no cwd is inside it.
- Do not size a candidate to decide its deletion: under IO load sizing is slow. Measure the result by the directory you removed, before and after, not by `df`: when several sessions clear at once the filesystem moves gigabytes between consecutive reads, so `df` tells you about the box and not about your cleanup. Where you do read `df`, read the filesystem the path is on, which `df <path>` gives: a removal under another mount leaves `/` unchanged.
- Never run one `du` of everything: it can run for hours. To find where the space went, what writes now, or what appeared in the last hours, use [references/finding-space.md](references/finding-space.md). Each instrument there is a named subcommand of `$S`, and each prints what its number does not measure.
- `du -d` and `du -t` bound what `du` PRINTS, not what it READS: to total a directory it stats every inode under it, so a bounded `du` over a whole multi-terabyte array is still a whole-array walk. On 2026-11-01 `du -x -d2 -t 2G /mnt/nvme ...` ran over two hours on the 3.2 TB array without finishing. So size the paths you suspect, one at a time: `du -xsm <path>` on a named directory, run as a unit so it survives your session and loses every I/O race to real work, `systemd-run --user --unit=<name> -p Nice=19 -p IOWeight=10 bash -c 'du -xsm <paths> > <outfile>'`. Ask each session for its own `du -xsm` rather than walking everyone's trees from one place, and never run two walkers at once (the rule above).
- A write sample says who writes now, not who holds the space. `/proc/<pid>/io` `write_bytes` counts rewrites and deleted output on every filesystem, so the top writer in a 15-second sample is often a build that deletes as it goes. To rank owners, ask each session for the `du -xsm` of its own paths; to rank a process, sample the size of the files it holds open twice, minutes apart. On 2026-11-01 a figure of "4.21 GB written in four minutes" by the omp transcript tree was the size of every file whose mtime moved; its real growth was 0.49 GiB/h.
- A guard that checks free space only before a batch starts is a before-the-fact gate: a long batch started above the line keeps writing all the way down. Sample during the batch as well, with two numbers, a higher one to start and a lower one to abort what is running, because stopping a batch that has paid its setup is a different cost from not starting one.
- When many agents of one lead write to the same disk, one agent reads the free space and refuses for all of them. Each agent reading `df` for itself lets twenty agents see 31 GiB at once and all launch, which is how a volume falls 18 GiB in two minutes. Across leads, `scripts/disk-gate` is that one reader for the whole box: `disk-gate reserve <gib> <name>` takes a lock, counts what other lanes hold, and exits 1 with the numbers when the work would take the mount under its floor; `disk-gate release <name>` gives the space back, and a reservation expires after two hours so a lane that dies does not hold space for ever. Telling each lane the disk is clear does not work: on 2026-11-02 the fleet was told at 00:16Z and the volume was back under its floor at 00:51Z.
- A file with link count 2 or more frees nothing when one of its names is deleted. A cache and a data directory sharing inodes, or mutation copies made with `os.link`, look twice their real size in a per-path `du`. Measure every path that may share inodes in one `du` call, which counts each inode once, before you promise anyone the space back.
- When a leaked test or process causes the space or load, read [references/test-leaks.md](references/test-leaks.md).

## Phase 2: Check-in

Send the check-in message to each session `envoy_sessions` lists with a title on this machine (not subagents: their parent answers for them), then build the release and protected files. The message and those steps are in [references/check-in.md](references/check-in.md). Do not skip the check-in because it is slow: most sessions answer in minutes, and their replies change the plan.

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
| STALE_REGISTRATION | registered, no directory | forget if `NAME@` is merged and the jj operation that registered the workspace is older than `--fresh-hours` |
| UNOBSERVABLE | outside what this process sees | keep, never planned |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | keep unless the owner releases it |

- Inside a box you see only that box's own tree (`inventory --box-dir auto` finds it under `/mnt/nvme/<user>/agentbox/boxes/` or `~/boxes/`), so a missing directory there proves nothing: judge absence from a host session (`inventory --box-dir none` there); `systemd-run --user` does not escape the box. Box `ps` ages (`etimes`, `lstart`) read 0 for every process, so read a process's age from the host.
- `jj workspace list` "(empty)" says nothing about unmerged ancestors. Use the classes.
- The freshness gate is the `.jj/working_copy/tree_state` mtime for a jj slot (`--fresh-hours`, default 24). For a plain directory it is the newest mtime in the top 3 levels, which one bounded walk finds. A directory's own mtime changes only when an entry appears in it or goes. The top-level stat this replaced read 160-280 h of idleness off trees with live work in them. A file deeper than 3 levels does not move the reading. So the idle hours are an upper bound on how stale a tree is. They are never proof that nothing writes in it: `$S procs` and `$S writers` give that proof. The plan lists every refusal in `refused`.
- A stale checkout's diff shows later merges as changes. Run `jj log -r '::@ ~ ::trunk()'` before you call it a revert.
- Leave alone: knives fork checkouts (use `knives finish`, never `rm`), Legion daemon state (`~/.local/state/legion/`, which holds the daemons' registry `legions.json`), and holds (paths a human said to keep, from the check-in).
- Never run a repo-wide `git worktree prune`: from a box, it deletes every other box's worktree entry. Before you forget a jj workspace, remove a git worktree, repair a worktree entry, or run `jj new` in a workspace you did not create, read [references/shared-git-store.md](references/shared-git-store.md).

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
```

- Apply runs once per store (a second one logs `locked`) and checks each item again before it acts. Right before a manual `rm`, check liveness again (`$S procs`, late check-in replies).
- Apply refuses to delete a folder another clone borrows objects from through `objects/info/alternates`, and the ledger names the clone. It finds those clones two ways, and its `borrower-scan` ledger line says what each found. One is the `agent-workspace-borrowers` record each source keeps. `~/.dotfiles/scripts/agent-workspace --separate-repo` writes a line there for each `git clone --shared` it makes, wherever that clone lives. The other is a walk of the places this box keeps a checkout, and it stops at each checkout root. Neither sees a clone inside another checkout's working tree that no record names. Before a manual `rm` of a folder that holds a git store, read that ledger line and the store's `agent-workspace-borrowers` file.
- Before you delete a finished session's scratch, search the transcripts of running sessions (`skill://session-attribution` gives their paths) for the path, and keep it if any task prompt names it.
- Before you delete a whole folder, `tar` every entry in it into an archive next to the ledger, not only the workspaces. For a jj-only workspace, archive the tree that `.jj/working_copy/tree_state` records, not only the files on disk.
- Run apply under `systemd-run --user`, because it can take hours and a tool timeout kills a foreground run. Launch notes, and how to undo a forget: [references/running-apply.md](references/running-apply.md).

## No recurring pass

- Never put this skill on a timer or cron: it needs the check-in. Run it by hand, and stop when done. What already runs on a timer, and what it leaves for this skill: [references/automatic-pruning.md](references/automatic-pruning.md).

## Phase 7: Report

Write a summary next to the ledger (apply writes one JSONL line per action; add a line for each manual action). Each apply line carries the used space of that path's own filesystem, and names its mount point. The summary gives space freed, what you kept and why (unpushed, holds, fresh), undescribed WIP in shared checkouts with the session `skill://session-attribution` finds for it (or "unknown"), and what you could not reclaim (locks, live owners). Give every sampled number its interval, because a watcher reports the extreme of its samples and not the extreme.
