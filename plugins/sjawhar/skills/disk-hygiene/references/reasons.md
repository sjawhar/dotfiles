# The reasons behind disk-hygiene's rules

Read this when you want to know why a rule in SKILL.md exists, or what happened when it was broken. Each section matches the SKILL.md section that states the rule, and quotes the rule as it stood with its reason and evidence. A reason whose topic has its own reference is in that reference: `finding-space.md` (du, df, writers, hardlinks, globs, `find`), `check-in.md`, `running-apply.md` (apply, borrowers, the ledger), `shared-git-store.md` (worktree locks), `box-mounts.md`, `docker.md` (root-owned files), `automatic-pruning.md` (timers).

## Safety rules

- Never delete a directory that a live session stands in: its shell and tools fail at once.
- Never delete content that exists only on disk (untracked, gitignored and modified tracked files all count): nothing can bring it back.
- Learn liveness from check-in replies and process cwds, because an idle live session looks the same on disk as a dead one. Never treat age or mtime as proof that a path is dead: a rebase rewrites every working copy's mtime, and idle sessions stay live. Use an age threshold only to keep a path longer; never use age to decide that a path may be deleted.

## The tool

Tool: `S=$SKILL/scripts/disk_hygiene.py`, with `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` (`skill://` does not resolve in bash, and a `find` under load can time out). `$S --help` lists subcommands and file formats. The script does the mechanical parts; you decide what is live and who owns it.

## Cleaning up after yourself

Here you delete only paths that you prove are yours. A name proves nothing either way, so never assume an unknown workspace has no user.

- Make each working directory that must outlive your session's `TMPDIR` with `work-dir <name>`. It makes `/mnt/nvme/<user>/tmp/<name>` with an `OWNER` file that names your session, so the tmp reaper and the box-health lead can tell it is yours (`devbox/session-scratch-reap.md`). A directory with no `OWNER` is never reaped and has no known owner after a loss (dispatch://INFRA-893). Never keep work in `/tmp` on sami-rocqperf: it is on the shared root disk, and `systemd-tmpfiles` empties it at every boot (`D /tmp` in `/usr/lib/tmpfiles.d/tmp.conf`); the reboot of 2026-10-31T22:35Z removed every entry.
- Make a second tree of a repository that is already on the box with `~/.dotfiles/scripts/agent-workspace --separate-repo` (a `git clone --shared`) or `git worktree add` from your clone, never with a second full clone, and remove each tree when its measurement or lane is done. On 2026-11-01, 16 full clones of one repository held about 38 GB of the same history on `/mnt/nvme`, and a census that added one 3.3 GB worktree every 2 minutes took the disk from 59 GiB free to 4 GiB in 12 minutes.
- In an agent box, your box's own tree (the parent of your workspace: `/mnt/nvme/<user>/agentbox/boxes/<your box>/` here, `~/boxes/<your box>/` on a machine without that disk — except the bind-mount sources below) and the box's `/tmp` are yours or your subagents'. On the host, every path needs the transcript test below.
- Any other path is yours only if your transcripts or your subagents' show the command that made it (`jj workspace add`, a clone, `mktemp`, a write). `skill://session-attribution` gives the transcript paths. If no transcript of yours names it, leave it and list it in your report.
- In your own box, also run `$S containers` and `$S images`, and treat a `registry` container in that output as yours: your subagents may have started it without your seeing it.

## Pacing (every phase)

- Run one `rm` or ad-hoc `du` at a time, under `sudo ionice -c3 nice -n19`. Parallel walkers plus an `rm` can hang every shell on the box.
- Never `lsof +D` or `fuser` a tree per candidate: each walks the whole tree. Before each item, run `$S procs` once and check that no cwd is inside it.
- A guard that checks free space only before a batch starts is a before-the-fact gate: a long batch started above the line keeps writing all the way down. Sample during the batch as well, with two numbers, a higher one to start and a lower one to abort what is running, because stopping a batch that has paid its setup is a different cost from not starting one.
- When many agents of one lead write to the same disk, one agent reads the free space and refuses for all of them. Each agent reading `df` for itself lets twenty agents see 31 GiB at once and all launch, which is how a volume falls 18 GiB in two minutes. Across leads, `scripts/disk-gate` is that one reader for the whole box: `disk-gate reserve <gib> <name>` takes a lock, counts what other lanes hold, and exits 1 with the numbers when the work would take the mount under its floor; `disk-gate release <name>` gives the space back, and a reservation expires after two hours so a lane that dies does not hold space for ever. `df` already shows what a holder has written, so the gate counts only the part of a reservation the holder has not reported with `disk-gate renew <name> --written <gib>`; a holder that does not report is counted at its full size, which makes the gate stricter, never looser. A renewal that does not raise a holder's count is never refused, so a holder can renew downward at any time. Telling each lane the disk is clear does not work: on 2026-11-02 the fleet was told at 00:16Z and the volume was back under its floor at 00:51Z.
- A reservation sized from what a build KEEPS under-reserves its transient by three times or more. On 2026-11-02 a 1.4M machcsl cut's freeze was reserved at 18 GiB, from the 12 GB its earlier cuts had kept. It wrote 12.3 GiB by 01:00Z, 22.2 by 01:27Z and 35.0 by 01:56Z, about 13 GiB each half hour, with no plateau, and it had not finished when its owner stopped it; it also builds the reference tree a second time, so its true peak is higher still. Measure a peak on a real run, in a pool pod where the scratch is the pool's, and bring back only the output.

## Phase 3: Classify

- Inside a box you see only that box's own tree (`inventory --box-dir auto` finds it under `/mnt/nvme/<user>/agentbox/boxes/` or `~/boxes/`), so a missing directory there proves nothing: judge absence from a host session (`inventory --box-dir none` there); `systemd-run --user` does not escape the box. Box `ps` ages (`etimes`, `lstart`) read 0 for every process, so read a process's age from the host.
- `jj workspace list` "(empty)" says nothing about unmerged ancestors. Use the classes.
- The freshness gate is the `.jj/working_copy/tree_state` mtime for a jj slot (`--fresh-hours`, default 24). For a plain directory it is the newest mtime in the top 3 levels, which one bounded walk finds. A directory's own mtime changes only when an entry appears in it or goes. The top-level stat this replaced read 160-280 h of idleness off trees with live work in them. A file deeper than 3 levels does not move the reading. So the idle hours are an upper bound on how stale a tree is. They are never proof that nothing writes in it: `$S procs` and `$S writers` give that proof. The plan lists every refusal in `refused`.
- A stale checkout's diff shows later merges as changes. Run `jj log -r '::@ ~ ::trunk()'` before you call it a revert.
- Leave alone: knives fork checkouts (use `knives finish`, never `rm`), Legion daemon state (`~/.local/state/legion/`, which holds the daemons' registry `legions.json`), and holds (paths a human said to keep, from the check-in).
