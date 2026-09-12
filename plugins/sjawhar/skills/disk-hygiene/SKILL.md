---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, `df` is far above what known work explains, load spikes with D-state processes, or jj workspaces / git worktrees / docker images / temp dirs have piled up from many agent sessions. Triggers - disk full, ENOSPC, "clean up the workspaces", stale worktrees, docker prune, buildx cache, /tmp full, io pressure, nightly hygiene.
---

# Disk Hygiene on a Shared Agent Box

Reclaim disk from a machine where dozens of agent sessions each left workspaces, containers, caches and scratch behind. The hard part is not deleting; it is knowing what is live. Sessions that are alive but idle look identical to dead ones on disk.

**Safety principle:** nothing is deleted whose directory any live session is standing in, and nothing is deleted whose only copy of versioned work would be lost. Liveness is *asked and measured*, never inferred from age.

Tool: `$SKILL/scripts/disk_hygiene.py` where `$SKILL` is the directory this SKILL.md loaded from (stdlib only, `--help` for subcommands). It does the mechanical parts; the judgment steps below are yours.

## Quick Reference

| Phase | Purpose | Gate |
|---|---|---|
| 1 | Baseline: `df`, IO pressure, ledger file | — |
| 2 | Envoy check-in with every live session | Replies drained; protected set written |
| 3 | Inventory + classify working copies | Every non-live row has a class |
| 4 | Apply: paced, one at a time, re-checking liveness | Ledger shows 0 errors |
| 5 | Docker: dangling, leaked buildkit leases, stale tags, orphaned sandboxes | Each container's owner known before removal |
| 6 | Caches and temp families | Only known families, only idle >24h |
| 7 | Report: freed, kept-and-why, unowned WIP found | Summary next to ledger |

Biggest levers observed, in order: dangling docker images, a buildkit cache whose leases leaked, unused tagged images, orphaned sandboxes, `/tmp` families, then workspaces. Workspaces are smaller than they look: `uv` venvs hardlink into `~/.cache/uv`, so 180 checkouts freed ~1.5 GB each.

## Pacing (applies to every phase)

- One `rm`/`du` at a time, always `sudo ionice -c3 nice -n19`. Two parallel `du` walkers plus one `rm` drove io-full to 70% and load to 500 on a 32-core box; every shell on the machine hung.
- Gate each deletion on `/proc/pressure/io` `full avg10 < 20` (the script does this).
- Never `lsof +D` or `fuser` a tree per candidate — that is a full walk each time. Liveness comes from one `/proc/*/cwd` snapshot (`disk_hygiene.py procs`), re-taken before each item.
- Sizing is optional. `df` before/after measures what was freed; do not block on `du` finishing.

## Phase 2: Check-in

Send every titled live session (`envoy_sessions`, skip subagents and other machines) one message:

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

Replies land one per turn; drain them with `envoy_inbox`. Record three lists: **live** (protected), **released** (delete even if a heuristic would keep it), **holds** (named by a human, e.g. "Sami said keep"). Two rules resolve conflicts: a live claim beats a release from someone else; an explicit hold beats everything.

Protected set = Envoy cwds ∪ every process cwd ∪ every reported live path ∪ holds. Write it as `{"protected": [...]}`; the script re-reads it before every item, so you can extend it mid-run when a late reply arrives.

## Phase 3: Classify

```bash
S=$SKILL/scripts/disk_hygiene.py
python3 $S inventory --repo ~/REPO --root ~/.worktrees --root /tmp > inv.json
python3 $S plan --inventory inv.json --protected protected.json > plan.json
```

| Class | Meaning | Action |
|---|---|---|
| LIVE | a process cwd is inside | never |
| UNPUSHED | non-empty commits not on any remote bookmark | keep; list for owner |
| PUSHED | every non-empty commit is on a remote bookmark | forget + rm (content is on origin and in the jj store) |
| MERGED | every non-empty ancestor is in trunk, `@` empty | forget + rm |
| UNREGISTERED | jj already forgot it; directory is residue | rm |
| STALE_REGISTRATION | registered, no directory | `jj workspace forget` if merged; else report |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | opt-in after `git status --porcelain` (slow) |

`jj workspace list` alone is not a safety signal: "(empty) (no description)" says nothing about the unmerged ancestors under it, and a described workspace may be fully pushed. Directory mtime is not a liveness signal either (a rebase op rewrites every working copy). The script uses `.jj/working_copy/tree_state` mtime only as a freshness tiebreaker (`--fresh-hours`, default 1).

Not in scope of the script — leave alone: knives-managed fork checkouts (release via `knives finish`, never rm), Legion daemon state, anything a human named as a hold. Removal mechanics the script uses: `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME`, then `rm`, then `git worktree prune` (workspaces in colocated repos are also git worktrees).

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl
```

Run it as a supervised background process, not a foreground call: 90 workspaces took ~2 h at idle IO priority. If you chain passes with a shell `while pgrep -f ...` loop, use a pattern that cannot match its own command line (`pgrep -f 'exec3[.]py'`), or the wrapper waits on itself forever.

## Phase 5: Docker

1. `docker system df` (no `-v`) failing with `rw layer snapshot not found for container X` is one dead container, not a reason to stop: `docker rm -f X`, rerun. `docker system df -v` walks every layer and stalls for minutes under IO load; use the script's `images`/`containers` instead.
2. `docker image prune -f` (dangling only). Always safe; was 292 GB.
3. Buildkit cache: `docker buildx du --builder B`. If Total is large but Reclaimable is 0 B, leases leaked from killed builds. Confirm no build is running (`docker top buildx_buildkit_B0` shows only buildkitd; no host `docker build`/`buildx`/`depot` process), then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`. Was 207 GB. Check which builder is the default (`docker buildx ls`, the `*`) before removing one.
4. Unused tagged images: `python3 $S images --older-than-hours 48` lists tags no container references. Remove by explicit `docker rmi REF` (no `-f`; a refusal means a container uses it). Leave anything younger for its owner; locally built images (no registry prefix) belong to whoever built them and may take an hour to rebuild.
5. Sandboxes: `python3 $S containers`. Reap only eval/task sandboxes (compose projects named for a run), never service containers (postgres, nats, registries, envoy, buildx) — those have no host holder by design. A sandbox is orphaned when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash` shell whose session is gone: kill the shell, then `docker compose -p PROJECT down -v`. When a live session is a plausible owner, ask before killing.
6. `docker volume prune -f` (anonymous unused only).

## Phase 6: Caches and temp

- `python3 $S tmp --older-than-hours 24 --protected protected.json` lists stale members of known temp families (dry run); add `--apply --ledger` to delete. Families only — a bare `/tmp/x` gets a human decision. Never delete by glob in a shared namespace on one owner's word (`/tmp/*.patch` belonged to three sessions).
- `uv cache prune` needs the exclusive lock; every `uv run` holds a shared lock for its lifetime, so on a busy box it times out. Run it in a quiet window with `UV_LOCK_TIMEOUT=3600`; never `--force`.
- Per-commit source caches (e.g. `~/.cache/<tool>/<sha>`): keep every sha still referenced by a remaining checkout's lockfile, delete the rest.

## Phase 7: Report

Ledger (JSONL, one line per action, with `df` after each) plus a summary: freed, kept-and-why (unpushed, holds, fresh), undescribed WIP found in shared checkouts and whose it might be, and anything that could not be reclaimed (locks, live owners). Files go next to the ledger under `~/.local/state/`.

## Common Mistakes

| Mistake | Reality |
|---|---|
| Parallel `du` to "size things first" | IO storm; sizes are not needed to decide, `df` measures the result |
| `lsof`/`fuser` per candidate | Same storm; one `/proc/*/cwd` snapshot answers it |
| Trusting `jj workspace list` "(empty)" | Says nothing about unmerged ancestors; use the revset classes |
| Trusting mtime for liveness | Rebases rewrite working copies; idle sessions have old mtimes |
| Skipping the check-in "because it is slow" | 33 of 34 sessions answered within 2 minutes; the replies moved 6 workspaces between delete and keep |
| A protected prefix as broad as `~/inspect` | Blocks the owner's own explicit releases under it; protect exact paths and use `subpath_release` for released children |
| `docker buildx prune --filter until=24h` on 0 B reclaimable | Leaked leases; restart buildkitd first |
| Diff of a stale checkout read as a revert | A checkout parented on an old main shows every later merge as "changes"; check `jj log -r '::@ ~ ::trunk()'` before alarming anyone |
| Deleting a live cwd | Its tools fail with "Working directory does not exist"; re-check liveness immediately before `rm`, not at inventory time |
