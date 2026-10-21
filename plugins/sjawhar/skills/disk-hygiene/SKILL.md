---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, or `df` is far above what known work explains. Also use on load spikes with D-state processes, or when workspaces, worktrees, docker images, build cache or temp dirs pile up from agent sessions. Also use to clean up after yourself. Triggers - disk full, ENOSPC, stale worktrees, missing git worktree entry, docker prune, buildx cache, /tmp full, io pressure, jj op store growth.
---

# Disk Hygiene on a Shared Agent Box

Idle live sessions look the same on disk as dead ones. The hard part is to know what is live.

## Safety rules

- Never delete a directory that a live session stands in.
- Never delete content that exists only on disk. Untracked, gitignored and modified tracked files all count.
- Ask and measure liveness. Never infer it from age or mtime.

Tool: `S=$SKILL/scripts/disk_hygiene.py`, with `SKILL=~/.dotfiles/plugins/sjawhar/skills/disk-hygiene` (`skill://` does not resolve in bash, and a `find` under load can time out). `$S --help` lists subcommands and file formats. The script does the mechanical parts. You own the judgment steps.

| Phase | Do | Gate |
|---|---|---|
| 1 | Baseline: `df`, IO pressure, ledger file | — |
| 2 | Check in with every live session | Replies drained, protected set written |
| 3 | Inventory and classify working copies | Every non-live row has a class |
| 4 | Apply, one item at a time | Ledger shows 0 errors |
| 5 | Docker | Each container's owner known before deletion |
| 6 | Caches and temp | Known families only, idle >24 h |
| 7 | Report | Summary next to the ledger |

Biggest levers, largest first: dangling images, buildkit cache with leaked leases, unused tagged images, orphaned sandboxes, `/tmp` families, workspaces. Workspaces free less than they look, because `uv` venvs hardlink into `~/.cache/uv`.

## Cleaning up after yourself

This is not a sweep. Delete only paths that you prove are yours. A name proves nothing either way, so never assume an unknown workspace has no user.

- In an agent box, `~/boxes/<your box>/` and the box's `/tmp` are yours or your subagents'. On the host, every path needs the next test.
- Any other path is yours only if your transcripts or your subagents' show the command that made it (`jj workspace add`, a clone, `mktemp`, a write). `session-attribution` gives the transcript paths. If no transcript of yours names it, leave it and tell whoever asked.
- Your own paths must still pass the safety rules: no live process in it (`$S procs`), no content only on disk.
- Check your own box also for things you "don't use" (registry, container census). You did not watch your subagents.
- A core dump is evidence before it is space. It can land in the crashed process's cwd. Identify it first: `file core`, `gdb -batch -ex bt core`. In a box, where `dmesg` and `journalctl` are unreadable, it can be the only crash evidence. A matching restart time does not prove which tool crashed.
- `~/boxes/<box>/jj-user-repos` and `omp-run` are the live sources of the box's `~/.config/jj/repos` and `~/.omp/run` bind mounts. They hold no repository but are not scratch. Their loss breaks `jj` or omp's daemon broker for the whole box. Name each entry that you delete. Never glob `~/boxes/<box>/*`.
  - Symptom: `jj` fails `Failed to determine the secure config for a repo` over `EROFS`. `findmnt -T <path>` or `/proc/self/mountinfo` shows `…/jj-user-repos//deleted`.
  - Fix: make the directory again, `sudo umount` the stale target, then `sudo mount --bind` it back. The directory alone does not reattach.

## Pacing (every phase)

- Run one `rm` or ad-hoc `du` at a time, under `sudo ionice -c3 nice -n19`. Parallel walkers plus an `rm` can hang every shell on the box.
- Delete only while `/proc/pressure/io` `full avg10 < 20`. The script gates on this.
- Never `lsof +D` or `fuser` a tree per candidate. Take one `/proc/*/cwd` snapshot (`$S procs`) again before each item.
- Do not size things to decide a deletion. `df` before and after measures the result.
- To find where the space went, run `sudo python3 $SKILL/scripts/disk_attribution.py --out <file.tsv> <roots>`. Use `systemd-run` when it can outlive a tool call. Never run one `du` of everything. It can run for hours.

## Phase 2: Check-in

Send this to each titled live session on this machine (`envoy_sessions`, no subagents). Do not skip it because it is slow. Most sessions answer in minutes, and their replies change the plan.

```
Disk cleanup check-in. I am inventorying every workspace/worktree/checkout on this box and
deleting what no live session needs. I will NOT touch any directory a live session reports as
in use. Please do NOT delete anything yourself; just answer.
Envoy shows your cwd as: <dir>
1. Your cwd (confirm/correct) plus any other checkout/dir you or your subagents use right now.
2. Dirs you created that are DONE and safe to delete (merged, PR closed, scratch). Paths.
3. Other dead weight you know of (eval logs, docker images/containers you own, caches).
"cwd only: <path>" is a complete answer. Silence after 20 min = cwd only.
```

Drain replies with `envoy_inbox`. One reply arrives per turn. Record three lists:

- **live**: protected.
- **released**: the owner gave the slot up. This is not a bypass. The script still guards it.
- **holds**: a human said to keep it.

A live claim beats a release from someone else. An explicit hold beats everything.

- Capture each release: `python3 $S release-capture --path P --repo R --kind git|jj [--name N] [--releases releases.json]`. Without `--releases`, paste the output into the file. `plan` only reports releases. `apply` acts on each once.
- Apply refuses a release if HEAD or the admin id changed, if HEAD reaches unpushed commits, or if content exists only on disk. A gitignored `.env` or `.venv/` blocks it. If only the directory mtime changed, apply keeps the entry for the next pass.
- Build the protected set: `python3 $S protected --fixed fixed.json > protected.json`. Put the check-in's live paths and holds under `"protected"` in the fixed file. The script adds bind-mount sources, knives checkouts and process cwds.
- Protect exact paths. A broad prefix like `~/inspect` blocks its owner's releases. Mark a released child `subpath_release`.
- Only the idle threshold protects a slot that runs through `jj -R` from elsewhere.
- The script reads the protected file again before each item. Add a late reply mid-run.

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
| STALE_REGISTRATION | registered, no directory | forget if `NAME@` merged and the op is older than `--fresh-hours` |
| UNOBSERVABLE | outside what this process sees | keep, never planned |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | keep unless the owner releases it |

- In a box, only its own `~/boxes/<host>` can show absence. Judge absence from a host session (`inventory --box-dir none` there). `systemd-run --user` does not escape the box. Box `ps` ages (`etimes`, `lstart`) read 0 for every process, so read a process's age from a session on the host.
- `jj workspace list` "(empty)" says nothing about unmerged ancestors. Use the classes.
- A rebase rewrites every working copy, so mtime is not liveness. The gate is the `.jj/working_copy/tree_state` mtime (`--fresh-hours`, default 24). The plan lists every refusal in `refused`.
- A stale checkout's diff shows later merges as changes. Run `jj log -r '::@ ~ ::trunk()'` before you call it a revert.
- Leave alone: knives fork checkouts (use `knives finish`, never `rm`), Legion daemon state, holds.

### Shared git store

- Never run a repo-wide `git worktree prune`. From a box, it deletes every other box's worktree entry. `shims/git` refuses a bare prune.
- Drop your own entry with `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME` or `git worktree remove <path>`.
- If entries go missing (an old build, or git outside the shim), run `git -C <ws> rev-parse --git-dir` on each kept slot. "is not a working tree" is the same loss.
- Before `jj workspace forget X`, run `cat X/.git`. Make sure no other live worktree's `.git` names the same admin dir. If one does, the forget breaks that sibling: its git fails with `fatal: not a git repository`, but its jj keeps working.
- To restore a missing entry, make `<store>/.git/worktrees/<name>/` with three files:
  - `gitdir`: absolute path of the workspace's `.git` file.
  - `commondir`: `../..`.
  - `HEAD`: output of `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`. If `@` is a non-empty described commit, run `jj new` first.
  
  Then run `git -C <ws> read-tree HEAD`, or the slot tracks nothing. Check `git -C <store> worktree list`, a non-zero `git -C <ws> ls-files | wc -l`, and no new sibling in `jj log -r '@ | @-'`.
- A lock, its reason string and a non-empty `@` all claim the tree is in use. Name the holder. Prove the work is complete before you delete or overwrite it, and name what you cannot prove. Do not `jj new` over another session's `@`. Never force another lane's lock (`-f -f`).

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
```

- Run it as a supervised background process. It can take hours. A `while pgrep -f` loop needs a pattern that cannot match itself (`'exec3[.]py'`).
- Under `systemd-run --user`, launch with `-E PATH="$PATH"`, or `jj` is not found. A script must keep the folder when a `jj` call fails.
- Apply runs once per store (a second one logs `locked`) and checks each item again before it acts. Before a manual `rm`, check liveness at that time.
- Before you delete a finished lane's scratch, make sure that no running agent's brief names the path.
- Before you delete a whole folder, archive every entry in it, not only the workspaces. Archive a jj-only workspace from its `tree_state` tree.
- `jj op revert <forget_op>` (from the ledger) restores a registration, not files, and does nothing if someone reused the name.

## No recurring pass

- `agentbox/devbox-prune` runs every 6 h where its timer is on. It does Phase 5 step 6 on the host daemon. In each box it does steps 2 and 6, a default-builder `docker builder prune -f`, and a cut of Go build cache idle >2 days. It never uses `-a`, deletes a tagged image or judges liveness. In a box, read its log with `systemd-run --user systemctl --user status devbox-prune.service`.
- agentbox deletes each session's workspace when omp exits.
- Never put this skill on a timer or cron. It covers the rest: unused tagged images, non-default builders, leaked leases, containers, boxes that filled before agentbox, Docker volumes, `~/.local/share/opencode`, a retired jj store. Run it by hand, with the check-in, and stop when done.

## Phase 5: Docker

1. If `docker system df` fails with `rw layer snapshot not found for container X`, run `docker rm -f X` and run it again. Never use `docker system df -v`. It stalls under IO load. Use `$S images` and `$S containers`.
2. `docker image prune -f` (dangling only). Always safe.
3. Buildkit: `docker buildx du --builder B`.
   - Large Total with 0 B Reclaimable means leaked leases. A `--filter until=24h` prune frees nothing then. First make sure that no build runs (`docker top buildx_buildkit_B0`, host `docker build`/`buildx`/`depot`). Then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`.
   - Find the default builder (`docker buildx ls`, the `*`) before you delete a builder.
   - Size the volumes before you blame test fixtures. Fixtures are rarely more than a few GB.
   - The standard cut is `docker buildx prune --builder B --keep-storage 20GB`. A builder that grows tens of GB/day needs a gc policy, not a sweep.
   - A dev-slot `deploy.py up` that builds its images leaves 25-40 GB per attempt. Reused published images leave a few GB. Check which before you size a disk floor. After a dev-slot proof, prune both builders and run `docker image prune -a` on the box daemon.
4. Tagged images: `python3 $S images --older-than-hours 48`. Delete each with `docker rmi REF`, without `-f`. A refusal means a container uses it. Leave younger ones for their owner. A local build (no registry prefix) belongs to its builder and can take an hour to rebuild.
5. Containers: `python3 $S containers` gives a family, owner and verdict ([containers.md](references/containers.md)). Labels attribute. They never decide liveness. Delete only what the verdict names, and run the census again before `rm`.
   - **local-stack** `idle`: leave it and message the owner.
   - **ryuk**, running: never delete it by hand. Find its client with `docker port <ryuk>` and `ss -tnp`. If that is an orphaned pytest, kill it, and ryuk cleans up.
   - **tc-fixture**: delete without its ryuk only at `orphan-no-ryuk`. Never race a fixture whose ryuk exists.
   - **agentbox**: never.
   - **other**: delete only eval or task sandboxes, never service containers (postgres, nats, registries, envoy, buildx). A sandbox is an orphan when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash`. Kill the shell, then `docker compose -p PROJECT down -v`. If a live session can be the owner, ask first.
6. `docker volume prune -f` (dangling only, attached to no container). Always safe.

## Phase 6: Caches and temp

- `python3 $S tmp --older-than-hours 24 --protected protected.json` lists stale temp families. Add `--apply --ledger` to delete. A bare `/tmp/x` needs a human decision. Never delete by glob in a shared namespace on one owner's word.
- Run `uv cache prune` in a quiet window with `UV_LOCK_TIMEOUT=3600`. Never `--force`.
- Per-commit caches (`~/.cache/<tool>/<sha>`): keep each sha that a remaining checkout's lockfile names. Delete the rest.
- Before you blame workspaces, check `.jj/repo/op_store` (`ls .jj/repo/op_store/views | wc -l`, per-view size). A stray `refs/pull/*` fetch adds `<n>@pr` bookmarks to each view. `jj git remote remove pr` drops them.
- Compact the op store with `python3 $SKILL/scripts/jj_opstore_gc.py --repo R --keep-days N --apply`, never plain `jj op abandon`. Without `--apply` it changes nothing. Run it in a quiet window. A concurrent jj commit can make a change divergent. The abandon cannot be undone, so on a shared store ask Sami to approve it first.

## Phase 7: Report

Write the ledger (JSONL, one line per action, `df` after each) and a summary next to it under `~/.local/state/`. The summary gives space freed and what you kept and why (unpushed, holds, fresh). It names undescribed WIP in shared checkouts and its possible owner, and what you could not reclaim (locks, live owners).

## Leaks from tests and processes

- A test that forks a detached server must reap it when the parent dies. A tool timeout is a SIGKILL that skips `finally`. Use a watchdog that polls the test's pid.
- Run each mutation-test mutant under a wall-clock `timeout`, in a process group that the harness kills on exit, with output capped (`ulimit -f`). A mutant without a guard can loop forever.
- A stopped process (`T` state) accepts `kill -TERM` with rc 0 and stays alive until `SIGCONT`. Read `ps -o stat=` before you trust a kill.
