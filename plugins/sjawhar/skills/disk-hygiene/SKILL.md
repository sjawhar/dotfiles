---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, `df` is far above what known work explains, load spikes with D-state processes, or jj workspaces / git worktrees / docker images / temp dirs have piled up from many agent sessions. Triggers - disk full, ENOSPC, "clean up the workspaces", stale worktrees, docker prune, buildx cache, /tmp full, io pressure.
---

# Disk Hygiene on a Shared Agent Box

Reclaim disk from a machine where dozens of agent sessions each left workspaces, containers, caches and scratch behind. The hard part is not deleting; it is knowing what is live. Sessions that are alive but idle look identical to dead ones on disk.

**Safety principle:** nothing is deleted whose directory any live session is standing in, and nothing is deleted that holds content existing only on disk — untracked, gitignored, and modified-tracked files all count, not just versioned work. Liveness is *asked and measured*, never inferred from age.

Tool: `$SKILL/scripts/disk_hygiene.py` where `$SKILL` is the directory this SKILL.md loaded from - on these machines `~/.dotfiles/plugins/sjawhar/skills/disk-hygiene`, because a `skill://` path does not resolve in bash and a `find` for the script is an IO walk that timed out at 180 s under load (2026-09-26) (stdlib only, `--help` for subcommands). It does the mechanical parts; the judgment steps below are yours.

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

## Cleaning up after yourself

A request to clean up after yourself is not a sweep. The whole question is which paths are yours, and a directory's name answers it in neither direction. On 2026-09-24 the platform PO deleted six workspaces, announced that five belonged to other agents, then retracted: "I asserted 'five of them were not mine' and that was an INFERENCE from the directory names, not a measurement." All six sat inside its own box, named for issues rather than sessions: "I read them as other agents' property because I did not recognise them." Sami, the same night: "do not assume that a workspace you do not recognize is unused."

Ownership is measured, like liveness:

- **Where it lives.** An agent box holds one session, so a path inside yours (`~/boxes/<your box>/`, the box's own `/tmp`) was made by you or by a subagent you spawned. A session running directly on the host has no such boundary, so every path takes the next test.
- **What created it.** For any other path (the shared jj store, a host-mounted home directory, anything a host session made), find the creating command in your own or your subagents' transcripts: a `jj workspace add`, a clone or `mktemp` into it, a write under it. `session-attribution` gives the transcript paths. A path no transcript of yours names is not yours, whatever it is called: say so to whoever asked, and leave it.

A CORE DUMP IS EVIDENCE BEFORE IT IS SPACE. `core_pattern` is often a bare `core`, so a multi-gigabyte core lands in the crashing process's working directory - a checkout root, where it reads as junk under disk pressure. Identify it before removing it: `file core` names the binary, and `gdb -batch -ex bt core` gives the stack. One lane deleted a 4.2 GiB core it had read as build residue, and only afterwards worked out it was its own harness crashing during a restart - which had also left that session's supervisor socket dead. Inside a box `dmesg` and `journalctl` are unreadable, so the deleted core was the only evidence (2026-09-26). A core whose timestamp matches a restart is not the last tool you happened to run.

The sweep's two gates still hold for your own paths: no live process standing in one (`disk_hygiene.py procs`), and no content that exists only on disk.

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

Replies land one per turn; drain them with `envoy_inbox`. Record three lists: **live** (protected), **released** (the owner explicitly gave the slot up — a release is still guarded, not a bypass), **holds** (named by a human, e.g. "Sami said keep"). Two rules resolve conflicts: a live claim beats a release from someone else; an explicit hold beats everything.

A released path is not a bare string. Capture the slot's identity — HEAD hash + worktree admin id + directory mtime — with `python3 $S release-capture --path P --repo R --kind git|jj [--name N] [--releases releases.json]`; `--releases` appends the entry to the releases file, otherwise paste the printed JSON in yourself. `plan --releases releases.json` reports each entry read-only; `apply --releases` acts on an entry once and retires it in place (one-shot). Apply still refuses when the identity no longer matches (HEAD/admin-id mismatch retires the entry as adoption evidence; an mtime-only change keeps it for the next pass), when unpushed commits are reachable from HEAD, or when any content exists only on disk — `git status --porcelain --ignored` non-empty for worktrees (a gitignored `.env` or `.venv/` blocks deletion), disk-vs-store divergence for jj slots. Every mutation of the releases file (retirement, capture append) holds an exclusive flock on the file itself, so concurrent mutators never lose each other's updates.

Start from the generated set — `python3 $S protected --fixed fixed.json > protected.json` (D4) — then merge in what the check-in surfaced. The generator regenerates liveness evidence per run: fixed entries ∪ container bind-mount sources (`docker inspect`, all containers) ∪ knives checkouts (the parent of a `default/`-layout path, so sibling workspace slots are covered; plus registry `workspaces` dirs) as `"protected"`, and every process cwd as an `"anchor"`. A protected entry guards both directions (under it or containing it); an anchor guards only the tree it stands IN — `/`, `$HOME` and `/tmp` are live cwds on every box, and subtree semantics for them would blanket-protect every slot. A source that cannot be read (docker down, knives missing, output unparseable) kills the run loudly rather than shrinking the set. Residual, stated in the plan: a slot driven only via `jj -R` from elsewhere has no cwd inside it and is protected by the idle threshold alone. Envoy replies, reported live paths and holds go into `"protected"` in the fixed/merged file; the script re-reads the file before every item, so you can extend it mid-run when a late reply arrives.

## Phase 3: Classify

```bash
S=$SKILL/scripts/disk_hygiene.py
python3 $S protected --fixed fixed.json > protected.json
python3 $S inventory --repo ~/REPO --root ~/.worktrees --root /tmp > inv.json
python3 $S plan --inventory inv.json --protected protected.json [--releases releases.json] > plan.json
```

| Class | Meaning | Action |
|---|---|---|
| LIVE | a process cwd is inside | never |
| UNPUSHED | non-empty commits not on any remote bookmark | keep; list for owner |
| PUSHED | every non-empty commit is on a remote bookmark | forget + rm (content is on origin and in the jj store) |
| MERGED | every non-empty ancestor is in trunk, `@` empty | forget + rm |
| UNREGISTERED | jj already forgot it; directory is residue | planned, but apply refuses removal: no working-copy commit to verify disk contents against |
| STALE_REGISTRATION | registered, no directory | `jj workspace forget` when `NAME@` is merged AND the registering op is older than `--fresh-hours`; refused when the op is unfindable. Unnamed git-side admin entries land in `refused` |
| UNOBSERVABLE | absent, but outside what this process can see: inside an agentbox, anything not under its own `~/boxes/<host>` dir (other boxes, the host's `~/.worktrees` and `/tmp`), and every name-only registration | keep; never planned. Absence is evidence only on the host (`systemd-run --user`) or with `--box-dir none` there. Added after 2026-09-25 09:15Z, when an in-box prune of the shared store broke four live worktrees |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | keep; reaped only via an owner release (apply then checks `git status --porcelain --ignored` itself) |

`jj workspace list` alone is not a safety signal: "(empty) (no description)" says nothing about the unmerged ancestors under it, and a described workspace may be fully pushed. Directory mtime is not a liveness signal either (a rebase op rewrites every working copy). `.jj/working_copy/tree_state` mtime is a hard freshness gate on every reapable class (`--fresh-hours`, default 24): a slot idle less than the threshold is skipped, and a slot whose idle is unmeasurable (no `tree_state` — exactly what a mid-creation slot looks like) is refused outright. Stale-registration forgets threshold the registering op's age the same way. Everything the plan declines to reap for a reason is listed in its `refused` array, never dropped.

Not in scope of the script — leave alone: knives-managed fork checkouts (release via `knives finish`, never rm), Legion daemon state, anything a human named as a hold. Removal mechanics the script uses: `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME` (which itself drops the colocated git-worktree linkage), then `rm`. It never runs `git worktree prune`: on a shared store a prune walks every slot and can delete other sessions' admin entries. When a released worktree's `git worktree remove` fails and the `rm` fallback runs, the script removes exactly that slot's `.git/worktrees/<admin-id>` entry and ledgers the result.

**A `forget` batch can end with OTHER slots' entries gone, and the cause is not established, so check your survivors afterwards.** Observed on one shared store: on 2026-09-24 a batch of eight `workspace forget`s (each printing `Missing HEAD at '.git/HEAD'`) and a second batch of five clean ones (`Removed Git worktree for "..."`) were each followed by two workspaces that had NOT been forgotten losing their `.git/worktrees/<name>` entries; on 2026-09-25 the store went 69 → 62 entries across a window whose op log held one `forget`, and one workspace's entry vanished twice between 00:50 and 01:05Z. jj kept working in every one (its own store is unaffected); what broke was every tool that shells out to plain git from inside them, which is how `gh pr create` died with `fatal: not a git repository: <store>/.git/worktrees/<name>`. **CONFIRMED BY AN OWNER, 2026-09-26: the cause is a store-wide `git worktree prune` run from the HOST.** A session working on the host (not inside a box) forgot its own workspaces and then ran `git -C <store> worktree prune` four times that night; from the host, every worktree living in a box's private `/tmp` looks like a missing directory, so prune removes each unlocked entry - which is exactly the observed signature, unlocked `/tmp` entries gone and locked ones spared. Its own conclusion, and the rule: the prunes were REDUNDANT, because `jj workspace forget` already removes that workspace's git worktree and prints `Removed Git worktree for ...` when it does. So remove your own entry with `jj workspace forget` and never run a store-wide prune; if you think you need one, run `git -C <store> worktree list` first and ask whether each "missing" path simply lives in a box you cannot see. NOTHING AUTOMATIC DOES THIS, so do not go looking for one: on a store with `gc.worktreePruneExpire never`, two unlocked and one locked worktree and all three directories hidden, none of `git worktree list`, `git worktree add` (with or without `--lock`), `git worktree repair`, `git status`, `git fetch`, `git gc --auto`, a full `git gc`, or `git maintenance run --auto` removed an entry, while the `git worktree prune` control removed both unlocked ones and spared the locked one; jj's own single prune call site sits in the unlink path that the fork patches out, and `jj util gc` shells to `git gc --prune=...`, which skips worktrees under that config (measured 2026-09-26). Measured the same night, ruling the jj builds out as the cause of this shape: of the builds still installed, stock 0.44.0 touches git worktrees not at all, and both remaining fork builds remove ONLY the forgotten workspace's own entry and leave an unseen unlocked sibling intact. **Both known causes are one action: a repo-wide `git worktree prune`,** which removes every *unlocked* entry whose path the caller cannot see (from inside a box, every other box's workspace). It runs explicitly, as a reviewer agent reported doing for tidy-up on 2026-09-25, or implicitly: an UNPATCHED BUILD OF UPSTREAM MAIN, whose `workspace forget` ran a repo-wide prune after unlinking. Not a release: 0.44.0, 0.45.0 and 0.45.1 create no git worktrees at all and carry no prune in `lib/src/git.rs`, and such a build still reports `0.45.1` because main's Cargo.toml says so, so the version string does not identify it (iac, measured 2026-09-26; upstream fix jj-vcs/jj#10166, open and unreviewed since 09-10). Sami ruled on it the same night (AGENTC-628, 04:10Z): "jj workspace forget should not be running git worktree prune, obviously" (fixed in the fork as `fix/forget-prune-only-own-worktree`, composed at `79dd935b`, build `0.45.1-sami.20260909-184010`; knives ledger, 2026-09-09: "any sibling worktree whose dir was momentarily unreadable lost its .git/worktrees/<name>"). The pinned build forgets only its own entry (scratch, 2026-09-25: an unseen unlocked entry, an unseen locked one and a same-basename sibling all survived), and the op log records a `forget` without the prune inside it. WHICH BUILDS PRUNE, measured per build on 2026-09-26 (iac; scratch repro: two colocated workspaces, hide w1's directory, `forget w2`): fork builds `20260909-181622` and `20260906-180417` DELETED w1's entry; `20260909-184010`, `20260910-010231` and the pin KEPT it. All five print the same `Removed Git worktree` line, so the output does not tell you which you ran. Those two pruning builds were uninstalled from the shared mise store that night, after checking that no process, crontab, systemd unit or `mise ls` source referenced them - `~/.mise` is mounted in every box, so that closes the hazard for every box unless one is reinstalled. This fits the 2026-09-26 02:50Z loss, without establishing it: a `jj workspace forget` ran in another box at 02:50:02Z and an unlocked `/tmp` worktree's entry was gone by 02:52:21Z, with no prune command in any session transcript. For tonight's losses, measured: the two `forget`s nearest them ran the fixed build, and `agentc-152-pair`/`-contract` lost their entries within a minute of creation with no jj `forget`, `gc` or workspace operation anywhere in the op log for that window, so their deleter was a git command outside jj; an explicit prune fits. None of these observations recorded whether the lost entries were locked or ruled out a concurrent prune (platform PO and the Legion redesign lane, 2026-09-25). **Check the slots you KEPT after every batch, unconditionally.** Check each slot you kept with `git -C <ws> rev-parse --git-dir`; a `git worktree lock <ws>` that answers `is not a working tree` on a directory you just created is the same loss, not a no-op (2026-09-25: a lane saw exactly that at 09:17 on a worktree the 09:15 prune had taken, read it as harmless, and found it broken mid-rebase half an hour later). Repair a missing one: if its `@` is a non-empty described commit, run `jj new` in it first (observed twice on 2026-09-25 by the platform-reports lane: after a rebuild with `HEAD` = `@-` under a non-empty `@`, the next `jj status` reset the working-copy parent and forked a duplicate `@`; not reproduced in a clean scratch repo, and harmless when `@` is empty), then recreate `<store>/.git/worktrees/<name>/` with three files — `gitdir` (absolute path to the workspace's own `.git` file), `commondir` (`../..`), and `HEAD` (that slot's current commit, from `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`) — then give the entry its index with `git -C <ws> read-tree HEAD` and confirm with `git -C <store> worktree list`, a `git -C <ws> rev-parse HEAD` a non-zero `git -C <ws> ls-files | wc -l`, and a `jj log -r '@ | @-'` that shows no new sibling. Without the index the slot resolves but tracks nothing: every `git ls-files` returns empty, so a test that reads tracked files fails with `parsed zero tracked files` instead of `not a git repository` (one repo's build-context test, reproduced 2026-09-24). Recreating your OWN slot's entry is cleaning up after yourself; `git worktree prune` stays banned on a shared store either way.

**One mechanism for that is now established: two worktrees sharing one admin dir.** Measured 2026-09-25 by the Reaper lane. The situation:
- `/tmp/wt/ce-reaper` and a live sibling whose path also ends in `ce-reaper` both pointed at `.git/worktrees/ce-reaper`; the sibling had adopted it during a content recovery.
- `jj workspace forget` on the throwaway, on the fixed build, removed only its own entry, as designed. But that entry was the sibling's admin dir, so the live workspace's git side broke: `git ls-files` returned `fatal: not a git repository`, while jj kept working.
- The survivor check above caught it, and the repair was the rebuild recipe below.

So before `jj workspace forget X`, run `cat X/.git` to see which admin dir X uses, and confirm no other live worktree's `.git` names the same one. Basename collisions are common: in one repo 33 box workspaces all ended in the same basename. This explains one of the observed losses, not necessarily all of them; keep the survivor check.

**A second mechanism is now established, and it exonerates `forget`.** Measured 2026-09-25 by the iac lane on a scratch reproduction: `jj workspace forget` does NOT drop other workspaces' git linkage. `git worktree prune` does — it removes unlocked registrations whose path looks missing, and from inside any box every other box's `ws/` path looks exactly that way, while locked ones are skipped. `scripts/agentbox` locks the boxes it creates for this reason, so the workspaces that keep losing their entries are the hand-made `jj workspace add` ones, which nobody locked. That is why a loss so often follows a `forget` without being caused by it: the prune is the natural tidy-up someone runs next. So lock a hand-made workspace immediately after adding it, and unlock it before removing it.

**The prune ban is load-bearing, not advisory.** From inside a box every other box's checkout directory does not exist, so a bare `git worktree prune` (correct on a single-checkout machine, and the natural tidy-up after removing your own throwaway worktree) deletes the admin entries of other sessions' live workspaces. Remove your own with `jj workspace forget` (on a build with the fork's fix, above) or `git worktree remove <path>`, and lock every workspace you add inside a box (`git -C <store> worktree lock <path> --reason '<why>'`): a lock is what a prune skips.

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
```

Run it as a supervised background process, not a foreground call: 90 workspaces took ~2 h at idle IO priority. If you chain passes with a shell `while pgrep -f ...` loop, use a pattern that cannot match its own command line (`pgrep -f 'exec3[.]py'`), or the wrapper waits on itself forever.

Apply is single-instance per store: a second apply finds the flock held, writes a `locked` ledger line, and exits. Every item is re-verified immediately before acting — existence, protection, live processes, class evidence, disk-vs-store divergence — so a plan gone stale refuses instead of deleting. A removed jj slot's ledger line carries `forget_op`: `jj op revert <forget_op>` restores the workspace registration only, never files, and reverting a forget whose name was reused since is a silent no-op.

## No recurring pass

There is no timer for this skill's reaper. (A separate host timer, `docker-volume-prune.timer` from dotfiles `511a158e`, has run `docker volume prune -f` every 6 h since 2026-09-25; that is Phase 5 step 6 on a schedule, not this reaper.) An hourly reaper (`disk-hygiene-reaper.timer`, 2026-09-15 to 2026-09-20) planned every hour and applied below a free-space floor; Sami retired it on 2026-09-20 ("I think we can kill the recurring disk cleanup thing. Agentbox is handling it better"). The reason it lost: it fought the symptom. A session's workspace, scratch and sandboxes leak because nothing owns their end; `agentbox` gives them an owner — the box is a jj workspace of a canonical checkout under `~/src/<repo>`, and when omp exits the launcher snapshots, forgets the workspace and removes the directory. What this skill is for now is the attended pass: a box that filled up before agentbox, or a pile agentbox does not own (Docker builders and volumes, `~/.local/share/opencode`, a retired jj store). Run it by hand, with the check-in, and stop when it is done.

## Phase 5: Docker

1. `docker system df` (no `-v`) failing with `rw layer snapshot not found for container X` is one dead container, not a reason to stop: `docker rm -f X`, rerun. `docker system df -v` walks every layer and stalls for minutes under IO load; use the script's `images`/`containers` instead.
2. `docker image prune -f` (dangling only). Always safe; was 292 GB.
3. Buildkit cache: `docker buildx du --builder B`. If Total is large but Reclaimable is 0 B, leases leaked from killed builds. Confirm no build is running (`docker top buildx_buildkit_B0` shows only buildkitd; no host `docker build`/`buildx`/`depot` process), then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`. Was 207 GB. Check which builder is the default (`docker buildx ls`, the `*`) before removing one. **Size the volumes before blaming fixtures:** on 2026-09-20 the "fixture leak" that grew Docker by 70 GB overnight was `buildx_buildkit_brave_curie0_state` at 92.6 GB (10.7 GB two days earlier) plus one agentbox volume at 47 GB; every test fixture on the box together was a few GB. `docker buildx prune --builder B --keep-storage 20GB` is the standard, non-breaking cut; a builder that grows 40 GB/day needs a gc policy on the builder, not a sweep. **An in-box dev-slot `deploy.py up` leaves ~25-40 GB of image layers per attempt on the box's own daemon, and nothing reaps it.** Measured 2026-09-25: three dev6 applies left ~83 GB (a 38.2 GB default-builder cache, a 7.2 GB deploy-builder cache, 37.5 GB of unused images), and pruning them freed 151 GB by `df` (reasoning UI lane). Reaper's proof left a 13 GB build cache. When the proof ends, prune both builders and run `docker image prune -a` on the box daemon; images of running containers are kept.
4. Unused tagged images: `python3 $S images --older-than-hours 48` lists tags no container references. Remove by explicit `docker rmi REF` (no `-f`; a refusal means a container uses it). Leave anything younger for its owner; locally built images (no registry prefix) belong to whoever built them and may take an hour to rebuild.
5. Containers: `python3 $S containers` classifies every container into a family with an owner attribution and a liveness verdict, using the fixture owners' own labels and rules (e2e owner, #19533, 2026-09-20). Labels attribute; they never decide liveness. Reap only what the verdict names and only after re-running the census at rm time:
   - **platform-e2e** (`trajectory.platform-e2e=1`, `.role` harness-db|test-fixture|cleanup-probe, `.run-id`): Created-never-started >1 h = `leaked` (a killed test skipped its `finally`). Running with a run-id = `live` iff some host process's environment carries `TRAJECTORY_PLATFORM_E2E_RUN_ID=<that id>`, else iff its postgres port has established clients. Running with an **empty** run-id (a developer's local run): the env walk matches *nothing* — another producer's id says nothing about this container — clients alone decide, docker's Created as the age grace. Unlabelled containers of the name shape are pre-#19533 producers: same rules, role `unlabelled`. Why no creating-PID label: a PID only means something inside the namespace that wrote it, and sessions run in agentboxes with their own PID namespaces (some with their own dockerd); a label that lies cross-namespace is worse than none. The client check is namespace-consistent with `docker ps` visibility: whoever sees the container is on the daemon its port binds to.
   - **local-stack** (`trajectory.local-stack{,.name,.owner}`; names `tl-platform-<name>-<owner>-db`): owner = `sha256("<uid>:<checkout>/platform/tl_platform")[:16]`, resolved to a checkout by hashing every plausible one on the box. `live` iff a process stands under that checkout or its port has clients; `idle` if the checkout exists but nothing runs (leave it — a dev stack is long-lived by design; message the owner); `leaked` if the checkout is gone. Created-never-started = `leaked`.
   - **ryuk** (`testcontainers-ryuk-<session>`): `stranded` when Created — it never ran, so no client socket ever existed; remove it together with its `org.testcontainers.session-id`-labelled fixtures (`tc-fixture` rows inherit the verdict). A **running** ryuk is never removed by hand: it self-reaps its session ~10 s after its client socket drops, so a live one means a live client — `docker port <ryuk>` then `ss -tnp` gives the client pid; if that is an orphaned pre-relocation pytest, kill the pid and ryuk does the rest. A `tc-fixture` whose named ryuk is **absent** (`orphan-no-ryuk`) is the same leak with a stronger mechanism: ryuk is testcontainers' only lifecycle owner, started before the fixtures and alive exactly as long as a client holds its socket, so no ryuk means no live owner can exist and self-reap never comes — reap it once it is past a 15-minute startup grace with zero clients (e2e owner, 2026-09-20; project-agnostic, it rests on testcontainers' contract). Never extend that to a fixture whose ryuk *exists*: an intact ryuk means an intact lifecycle, and racing it is how a live session's database dies.
   - **agentbox**: session infrastructure, `never`.
   - **other**: compose sandboxes and service containers. Reap only eval/task sandboxes (compose projects named for a run), never service containers (postgres, nats, registries, envoy, buildx) — those have no host holder by design. A sandbox is orphaned when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash` shell whose session is gone: kill the shell, then `docker compose -p PROJECT down -v`. When a live session is a plausible owner, ask before killing. Two attribution mistakes in one sweep (Created ryuks read as running; running Playwright harness DBs read as pytest husks) is what earned the labels — name-parsing stays as the fallback for pre-label producers only.
6. `docker volume prune -f` (dangling only — attached to no container). Always safe; was 26 GB.

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
| Aging a process from inside an agentbox | `ps -o etimes`/`lstart` read 0 / "now" for every process in a box, PID 1 included (measured 2026-09-25, hiring lane and librarian), so a hung process looks brand new; read its age from the host (`systemd-run --user ... ps`) |
| Skipping the check-in "because it is slow" | 33 of 34 sessions answered within 2 minutes; the replies moved 6 workspaces between delete and keep |
| A protected prefix as broad as `~/inspect` | Blocks the owner's own explicit releases under it; protect exact paths and use `subpath_release` for released children |
| `docker buildx prune --filter until=24h` on 0 B reclaimable | Leaked leases; restart buildkitd first |
| Diff of a stale checkout read as a revert | A checkout parented on an old main shows every later merge as "changes"; check `jj log -r '::@ ~ ::trunk()'` before alarming anyone |
| Deleting a live cwd | Its tools fail with "Working directory does not exist"; re-check liveness immediately before `rm`, not at inventory time |
| Freeing scratch a running agent's brief names | No process holds it yet, so the liveness check passes, and the agent then fails on its missing inputs. Before deleting a finished lane's scratch, check that no running agent's brief names the path (2026-09-25: one lane deleted its own fixer's repro files 20 min after dispatching it; another freed a live reviewer's clone mid-sweep) |

## The shared jj op store (`.jj/repo/op_store`)

Every jj operation stores a full view (all bookmarks + remote bookmarks + tags + per-workspace
working-copy commits). At agent scale this is the box's fastest-growing pile: one repo measured
2026-09-19 at ~180 workspaces / ~7k ops/day / 1-3 MB per view = 10-25 GB/day, 187 GB total; a
single stray `git fetch '+refs/pull/*/head:refs/remotes/pr/*'` in the shared store tripled every
view (10,907 `<n>@pr` bookmarks) until `jj git remote remove pr` dropped them. Watch
`ls .jj/repo/op_store/views | wc -l` and the per-view size before blaming workspaces.

`scripts/jj_opstore_gc.py --repo R --keep-days N [--apply]` compacts it without stranding
workspaces: abandon older-than-N history, remap every workspace's `checkout` file to the
reparented operation under jj's own working-copy lock (plain `jj op abandon` gives every other
workspace "Run `jj workspace update-stale`" + a RECOVERY COMMIT), re-abandon chains that
concurrent commands re-attach, and sweep unreachable op/view files only after every jj process
that predates the abandon has exited (`--sweep-only` resumes a deferred sweep). Known hazard,
reproduced on a scratch repo: a jj command that loads pre-abandon state and commits AFTER the
remap makes the affected change divergent when a reconcile merges the chains (base = root).
The tool re-abandons within its poll interval, but a genuinely quiet window is the safe run
condition — and `jj op abandon` is NOT undoable, so on a shared store it is Sami's call.
