---
name: disk-hygiene
description: Use when a shared dev box is low on disk or RAM, `df` is far above what known work explains, load spikes with D-state processes, or jj workspaces / git worktrees / docker images / temp dirs have piled up from many agent sessions. Triggers - disk full, ENOSPC, "clean up the workspaces", stale worktrees, docker prune, buildx cache, /tmp full, io pressure.
---

# Disk Hygiene on a Shared Agent Box

Reclaim disk from a machine where dozens of agent sessions each left workspaces, containers, caches and scratch behind. The hard part is not deleting; it is knowing what is live. Sessions that are alive but idle look identical to dead ones on disk.

**Safety principle:** nothing is deleted whose directory any live session is standing in, and nothing is deleted that holds content existing only on disk — untracked, gitignored, and modified-tracked files all count, not just versioned work. Liveness is *asked and measured*, never inferred from age.

Tool: `$SKILL/scripts/disk_hygiene.py` where `$SKILL` is the directory this SKILL.md loaded from - on these machines `~/.dotfiles/plugins/sjawhar/skills/disk-hygiene`, because a `skill://` path does not resolve in bash and a `find` for the script is an IO walk that can time out under load (stdlib only, `--help` for subcommands). It does the mechanical parts; the judgment steps below are yours.

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

A request to clean up after yourself is not a sweep. The whole question is which paths are yours, and a directory's name — including one that looks like another agent's or is named for an issue rather than a session — answers it in neither direction: do not assume a workspace you don't recognize is unused.

Ownership is measured, like liveness:

- **Where it lives.** An agent box holds one session, so a path inside yours (`~/boxes/<your box>/`, the box's own `/tmp`) was made by you or by a subagent you spawned. A session running directly on the host has no such boundary, so every path takes the next test. **But "inside my box" does not mean "scratch": `~/boxes/<box>/jj-user-repos` and `~/boxes/<box>/omp-run` are live sources of bind mounts** the box serves at `~/.config/jj/repos` and `~/.omp/run`, and deleting either (including by globbing `~/boxes/<box>/*`) breaks `jj`, or every omp tool call that goes through the daemon broker, for every session on the box — neither has a `.git` or a jj root, so a guard phrased as "holds no repository" reads it as plain scratch. Enumerate the names you mean to delete; never glob. The symptom is a stale bind mount, not corruption: `jj` fails `Failed to determine the secure config for a repo` with `EROFS` underneath, `/proc/self/mountinfo` names it (`…/jj-user-repos//deleted`), and `findmnt -T <path>` is the fastest way to confirm it. Recreate the directory, `sudo umount` the stale target, then `sudo mount --bind` it back — recreating the directory alone does not reattach.
- **What created it.** For any other path (the shared jj store, a host-mounted home directory, anything a host session made), find the creating command in your own or your subagents' transcripts: a `jj workspace add`, a clone or `mktemp` into it, a write under it. `session-attribution` gives the transcript paths. A path no transcript of yours names is not yours, whatever it is called: say so to whoever asked, and leave it.

The sweep's two gates still hold for your own paths: no live process standing in one (`disk_hygiene.py procs`), and no content that exists only on disk.

**"I don't use X" is not a reason to skip a free check of X on your own box**: what your subagents did there is yours, and you did not watch them do it — a registry check or container census is cheap, and the self-assessment is the unreliable part. **A core dump is evidence before it is space.** `core_pattern` is often a bare `core`, so a multi-gigabyte core can land in the crashing process's working directory and read as junk under disk pressure — identify it before removing it: `file core` names the binary, `gdb -batch -ex bt core` gives the stack. Inside a box, `dmesg` and `journalctl` are unreadable, so a core may be the only evidence of what crashed; a core's timestamp matching a restart is a signal, not proof of which tool produced it.

## Pacing (applies to every phase)

- One `rm` or ad-hoc `du` at a time, always `sudo ionice -c3 nice -n19`. Parallel `du` walkers plus a concurrent `rm` can drive IO to full and load into the hundreds on a many-core box, hanging every shell on the machine. The attribution tool below runs several `du` processes only because it pauses new ones on IO pressure.
- Gate each deletion on `/proc/pressure/io` `full avg10 < 20` (the script does this).
- Never `lsof +D` or `fuser` a tree per candidate — that is a full walk each time. Liveness comes from one `/proc/*/cwd` snapshot (`disk_hygiene.py procs`), re-taken before each item.
- Sizing is optional for deciding a deletion: `df` before/after measures what was freed; do not block on `du` finishing.
- To answer *where the space went*, run `sudo python3 $SKILL/scripts/disk_attribution.py --out <file.tsv> <roots>` (under `systemd-run` when it may outlive a tool call), never one `du` of everything. It sizes sibling directories in batches, one `du -sx` per batch and several batches at a time, under a deadline (10 s by default). `du` prints each directory's total as soon as it finishes, in order. So at the deadline every printed total is exact, the directory `du` was still inside is the large one and is split into child batches, and the directories it never reached are queued again. Large subtrees therefore surface first, while the small ones drop out. New batches pause while `/proc/pressure/io` `full avg10` is above 20. `--out` records every result, so per-directory totals can be summed mid-run. A hardlinked file shared by two batches counts in both, so check the roll-up against `df`. A single `du -x /` on a multi-TB host can run for hours without finishing; this tool's batching sizes most of a large host in minutes.

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
| UNOBSERVABLE | absent, but outside what this process can see: inside an agentbox, anything not under its own `~/boxes/<host>` dir (other boxes, the host's `~/.worktrees` and `/tmp`), and every name-only registration | keep; never planned. Absence is evidence only from a session actually running on the host, or with `--box-dir none` there. **`systemd-run --user` does not escape a box**: inside one it runs under the box's own user manager and namespace, so it returns a box-scoped answer that reads host-scoped (a box-scoped `ps` can show a single-digit process count against dozens visible from the host). Ask a host session rather than infer. |
| GIT_HEAD_PUSHED | plain git worktree, HEAD on a remote | keep; reaped only via an owner release (apply then checks `git status --porcelain --ignored` itself) |

`jj workspace list` alone is not a safety signal: "(empty) (no description)" says nothing about the unmerged ancestors under it, and a described workspace may be fully pushed. Directory mtime is not a liveness signal either (a rebase op rewrites every working copy). `.jj/working_copy/tree_state` mtime is a hard freshness gate on every reapable class (`--fresh-hours`, default 24): a slot idle less than the threshold is skipped, and a slot whose idle is unmeasurable (no `tree_state` — exactly what a mid-creation slot looks like) is refused outright. Stale-registration forgets threshold the registering op's age the same way. Everything the plan declines to reap for a reason is listed in its `refused` array, never dropped.

Not in scope of the script — leave alone: knives-managed fork checkouts (release via `knives finish`, never rm), Legion daemon state, anything a human named as a hold. Removal mechanics the script uses: `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME` (which itself drops the colocated git-worktree linkage), then `rm`. It never runs `git worktree prune`: on a shared store a prune walks every slot and can delete other sessions' admin entries. When a released worktree's `git worktree remove` fails and the `rm` fallback runs, the script removes exactly that slot's `.git/worktrees/<admin-id>` entry and ledgers the result.

**A repo-wide `git worktree prune` is the only known cause of OTHER slots' admin entries disappearing after a `forget`, and it is banned.** It removes every *unlocked* worktree registration whose path the caller cannot see — from inside a box, that's every other box's workspace — even though `jj workspace forget` already removes its own entry and never touches anyone else's. **The ban is enforced, not advisory: `shims/git` refuses a bare `git worktree prune` in an agent session** (`-n` still runs), locks every `git worktree add`, and unlocks on `git worktree remove`. Remove your own entry with `jj workspace forget`, or `git worktree remove <path>`; never a store-wide prune. If you still find entries missing (an unpatched build, or a manual git call outside the shim), check every slot you kept with `git -C <ws> rev-parse --git-dir` — an answer of "is not a working tree" is the same loss, not a no-op.

**A second cause: two worktrees sharing one admin dir** (a basename collision, common when many boxes create workspaces with the same name). `jj workspace forget` on one removes only its own entry as designed, but if that entry is actually the live sibling's admin dir, the sibling's git side breaks (`git ls-files` returns `fatal: not a git repository`) while jj itself keeps working. Before `jj workspace forget X`, run `cat X/.git` to see which admin dir X uses, and confirm no other live worktree's `.git` names the same one.

**Repair a missing entry** by recreating `<store>/.git/worktrees/<name>/` with three files — `gitdir` (absolute path to the workspace's own `.git` file), `commondir` (`../..`), and `HEAD` (the slot's current commit, from `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`) — then rebuild its index with `git -C <ws> read-tree HEAD` and confirm with `git -C <store> worktree list`, a non-zero `git -C <ws> ls-files | wc -l`, and `jj log -r '@ | @-'` showing no new sibling. Without the index the slot resolves but tracks nothing, so a test reading tracked files fails with "parsed zero tracked files" instead of "not a git repository". If `@` is a non-empty described commit, run `jj new` in the workspace first.

**A test that forks a detached server must reap it on parent death, not only in teardown.** An agent harness's tool timeout is SIGKILL-shaped, so a run killed mid-test never reaches its `finally` — a watchdog polling the test's own pid reaps where teardown cannot.

**A mutation test that removes a loop guard turns bounded input into unbounded output; cap each mutant's runtime and output, not only its exit.** A type guard replaced by a no-op can turn a bounded `jq` loop unbounded on input it would otherwise reject (jq orders numbers before strings, so a range bound by a string value never terminates), and if the harness doesn't kill the mutant's whole process group, a reparented mutant can write to disk unboundedly for hours. Run every mutant under a wall-clock `timeout`, in its own process group the harness kills on any exit, with its output size-capped (`ulimit -f`, or a capped output path).

**A STOPPED process holds `SIGTERM` until it continues, so `kill` returning 0 proves nothing.** A process in state `T` accepts `kill -TERM` with rc 0, stays alive, and dies only on `SIGCONT` — read `ps -o stat=` for `T` before believing a kill landed.

**A lock, its reason string, and a non-empty `@` on a shared checkout are all claims someone is using the tree — name the holder and prove the work is complete before removing or overwriting one.** A locked tree's reason string is free text, not proof; verify each holder's merge or completion evidence, and name what you can't prove. A non-empty `@` left by another session's uncommitted work is the same claim even with no lock object present — advancing it (e.g. `jj new` over it) is the overwrite-without-reading move. Don't repair either by forcing: `git worktree remove` on a locked tree wants `-f -f`, and double-forcing another lane's explicit lock compounds the error; a store-wide `prune` is banned regardless.

## Phase 4: Apply

```bash
nice -n19 python3 $S apply --plan plan.json --protected protected.json --ledger ledger.jsonl [--releases releases.json]
```

Run it as a supervised background process, not a foreground call: 90 workspaces took ~2 h at idle IO priority. If you chain passes with a shell `while pgrep -f ...` loop, use a pattern that cannot match its own command line (`pgrep -f 'exec3[.]py'`), or the wrapper waits on itself forever.

Apply is single-instance per store: a second apply finds the flock held, writes a `locked` ledger line, and exits. Every item is re-verified immediately before acting — existence, protection, live processes, class evidence, disk-vs-store divergence — so a plan gone stale refuses instead of deleting. A removed jj slot's ledger line carries `forget_op`: `jj op revert <forget_op>` restores the workspace registration only, never files, and reverting a forget whose name was reused since is a silent no-op.

## No recurring pass

There is no timer for this skill's reaper itself. The always-safe class runs automatically: `agentbox/devbox-prune` runs every 6 h from `agentbox/devbox-prune.timer` on the shared devbox, and in every running agent box on that box's own docker daemon. Each run does Phase 5 step 6 on the host daemon; in each box, steps 2 and 6 plus `docker builder prune -f` (dangling build cache of the default builder only — an empty `--config` so a box's `builder: depot` alias can't hijack it) and deletes host Go build cache entries idle more than 2 days (Go itself only trims `~/.cache/go-build` after 5 idle days with no size cap). It never runs `-a`, never removes a tagged image, and never decides whether a workspace or image is live. It logs bytes reclaimed per box and for the Go cache; read a failing unit's log from the host, or from inside a box via `systemd-run --user systemctl --user status devbox-prune.service` (`journalctl` has no journal files inside a box).

What it leaves is still this skill's: unused tagged images, a box's non-default builders, leaked buildkit leases, containers. A session's own workspace, scratch and sandboxes are owned by `agentbox` instead of a recurring reaper — the box is a jj workspace of a canonical checkout, and when omp exits the launcher snapshots, forgets the workspace and removes the directory; ownership, not a periodic sweep, is what ends that leak. This skill is the attended pass for what agentbox doesn't own: a box that filled up before agentbox existed, or a pile outside its scope (Docker builders and volumes, `~/.local/share/opencode`, a retired jj store). Run it by hand, with the check-in, and stop when it is done.

## Phase 5: Docker

1. `docker system df` (no `-v`) failing with `rw layer snapshot not found for container X` is one dead container, not a reason to stop: `docker rm -f X`, rerun. `docker system df -v` walks every layer and stalls for minutes under IO load; use the script's `images`/`containers` instead.
2. `docker image prune -f` (dangling only). Always safe; was 292 GB.
3. Buildkit cache: `docker buildx du --builder B`. If Total is large but Reclaimable is 0 B, leases leaked from killed builds. Confirm no build is running (`docker top buildx_buildkit_B0` shows only buildkitd; no host `docker build`/`buildx`/`depot` process), then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`. Was 207 GB. Check which builder is the default (`docker buildx ls`, the `*`) before removing one. **Size the volumes before blaming test fixtures** — most Docker growth on a box comes from the buildkit cache or image layers, not from test fixtures, which rarely total more than a few GB together. `docker buildx prune --builder B --keep-storage 20GB` is the standard, non-breaking cut; a builder that grows tens of GB/day needs a gc policy on the builder, not a recurring sweep. **An in-box dev-slot `deploy.py up` that BUILDS the slot's images can leave 25-40 GB of image layers per attempt on the box's own daemon; one that reuses already-published images at their existing digests leaves only a few GB of deploy-builder cache** — check which happened before sizing a disk floor around it. `devbox-prune.timer` (above) reaps only the dangling part of that every 6 h (the default builder's dangling cache and dangling images), not the deploy builder or unused tagged images; when a dev-slot's proof ends, prune both builders and run `docker image prune -a` on the box daemon (images of running containers are kept).
4. Unused tagged images: `python3 $S images --older-than-hours 48` lists tags no container references. Remove by explicit `docker rmi REF` (no `-f`; a refusal means a container uses it). Leave anything younger for its owner; locally built images (no registry prefix) belong to whoever built them and may take an hour to rebuild.
5. Containers: `python3 $S containers` classifies every container into a family with an owner attribution and a liveness verdict, using the fixture owners' own labels and rules. Labels attribute; they never decide liveness. Reap only what the verdict names and only after re-running the census at rm time:
   - **platform-e2e** (`trajectory.platform-e2e=1`, `.role` harness-db|test-fixture|cleanup-probe, `.run-id`): Created-never-started >1 h = `leaked` (a killed test skipped its `finally`). Running with a run-id = `live` iff some host process's environment carries `TRAJECTORY_PLATFORM_E2E_RUN_ID=<that id>`, else iff its postgres port has established clients. Running with an **empty** run-id (a developer's local run): the env walk matches *nothing* — another producer's id says nothing about this container — clients alone decide, docker's Created as the age grace. Unlabelled containers of the name shape are pre-labeling producers: same rules, role `unlabelled`. Why no creating-PID label: a PID only means something inside the namespace that wrote it, and sessions run in agentboxes with their own PID namespaces (some with their own dockerd); a label that lies cross-namespace is worse than none. The client check is namespace-consistent with `docker ps` visibility: whoever sees the container is on the daemon its port binds to.
   - **local-stack** (`trajectory.local-stack{,.name,.owner}`; names `tl-platform-<name>-<owner>-db`): owner = `sha256("<uid>:<checkout>/platform/tl_platform")[:16]`, resolved to a checkout by hashing every plausible one on the box. `live` iff a process stands under that checkout or its port has clients; `idle` if the checkout exists but nothing runs (leave it — a dev stack is long-lived by design; message the owner); `leaked` if the checkout is gone. Created-never-started = `leaked`.
   - **ryuk** (`testcontainers-ryuk-<session>`): `stranded` when Created — it never ran, so no client socket ever existed; remove it together with its `org.testcontainers.session-id`-labelled fixtures (`tc-fixture` rows inherit the verdict). A **running** ryuk is never removed by hand: it self-reaps its session ~10 s after its client socket drops, so a live one means a live client — `docker port <ryuk>` then `ss -tnp` gives the client pid; if that is an orphaned pre-relocation pytest, kill the pid and ryuk does the rest. A `tc-fixture` whose named ryuk is **absent** (`orphan-no-ryuk`) is the same leak with a stronger mechanism: ryuk is testcontainers' only lifecycle owner, started before the fixtures and alive exactly as long as a client holds its socket, so no ryuk means no live owner can exist and self-reap never comes — reap it once it is past a 15-minute startup grace with zero clients. Never extend that to a fixture whose ryuk *exists*: an intact ryuk means an intact lifecycle, and racing it is how a live session's database dies.
   - **agentbox**: session infrastructure, `never`.
   - **other**: compose sandboxes and service containers. Reap only eval/task sandboxes (compose projects named for a run), never service containers (postgres, nats, registries, envoy, buildx) — those have no host holder by design. A sandbox is orphaned when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash` shell whose session is gone: kill the shell, then `docker compose -p PROJECT down -v`. When a live session is a plausible owner, ask before killing.
6. `docker volume prune -f` (dangling only — attached to no container). Always safe; was 26 GB. `agentbox/devbox-prune` does this on the host daemon and in every running agent box (every 6 h on the shared devbox, where its timer is enabled by hand).

## Phase 6: Caches and temp

- `python3 $S tmp --older-than-hours 24 --protected protected.json` lists stale members of known temp families (dry run); add `--apply --ledger` to delete. Families only — a bare `/tmp/x` gets a human decision. Never delete by glob in a shared namespace on one owner's word (`/tmp/*.patch` belonged to three sessions).
- `uv cache prune` needs the exclusive lock; every `uv run` holds a shared lock for its lifetime, so on a busy box it times out. Run it in a quiet window with `UV_LOCK_TIMEOUT=3600`; never `--force`.
- Per-commit source caches (e.g. `~/.cache/<tool>/<sha>`): keep every sha still referenced by a remaining checkout's lockfile, delete the rest.

## Phase 7: Report

Ledger (JSONL, one line per action, with `df` after each) plus a summary: freed, kept-and-why (unpushed, holds, fresh), undescribed WIP found in shared checkouts and whose it might be, and anything that could not be reclaimed (locks, live owners). Files go next to the ledger under `~/.local/state/`.

## Common Mistakes

| Mistake | Reality |
|---|---|
| Unbounded parallel `du` walkers to "size things first" | IO storm. Deciding a deletion needs no sizes (`df` measures the result); attributing space uses `disk_attribution.py`, one process whose walks are time-boxed and pressure-gated |
| `lsof`/`fuser` per candidate | Same storm; one `/proc/*/cwd` snapshot answers it |
| Trusting `jj workspace list` "(empty)" | Says nothing about unmerged ancestors; use the revset classes |
| Trusting mtime for liveness | Rebases rewrite working copies; idle sessions have old mtimes |
| Aging a process from inside an agentbox | `ps -o etimes`/`lstart` read 0 / "now" for every process in a box, PID 1 included, so a hung process looks brand new; read its age from a session running on the host. `systemd-run --user ... ps` from inside the box does NOT escape this — it still answers from the box's own namespace |
| Skipping the check-in "because it is slow" | 33 of 34 sessions answered within 2 minutes; the replies moved 6 workspaces between delete and keep |
| A protected prefix as broad as `~/inspect` | Blocks the owner's own explicit releases under it; protect exact paths and use `subpath_release` for released children |
| `docker buildx prune --filter until=24h` on 0 B reclaimable | Leaked leases; restart buildkitd first |
| Diff of a stale checkout read as a revert | A checkout parented on an old main shows every later merge as "changes"; check `jj log -r '::@ ~ ::trunk()'` before alarming anyone |
| Deleting a live cwd | Its tools fail with "Working directory does not exist"; re-check liveness immediately before `rm`, not at inventory time |
| Freeing scratch a running agent's brief names | No process holds it yet, so the liveness check passes, and the agent then fails on its missing inputs. Before deleting a finished lane's scratch, check that no running agent's brief names the path |
| A cleanup script under `systemd-run --user` calling `jj` | A user unit's PATH holds only the system directories, so `jj` (a dotfiles shim over mise) is not found, and a check like `jj workspace list \| grep -q` reads that as "not registered," risking folder removal while registrations survive. Launch with `-E PATH="$PATH"`, and have a script keep the folder when a `jj` call fails rather than treat the failure as a miss |
| Archiving only the workspaces in a folder you then remove whole | Lanes leave logs, ledgers and reports beside their workspaces, not just inside them. Archive every entry in the folder, and a jj-only workspace (no `.git`) against the tree in its `.jj/working_copy/tree_state` |
| Globbing `~/boxes/<box>/*` because it is "your own box" | Not all of it is scratch — see the bind-mount note (`jj-user-repos`, `omp-run`) under "Where it lives" above; enumerate names instead of globbing |

## The shared jj op store (`.jj/repo/op_store`)

Every jj operation stores a full view (all bookmarks + remote bookmarks + tags + per-workspace
working-copy commits). At agent scale this is the box's fastest-growing pile: roughly 180
workspaces / ~7k ops/day / 1-3 MB per view can run 10-25 GB/day; a single stray
`git fetch '+refs/pull/*/head:refs/remotes/pr/*'` in the shared store can triple every view with
thousands of `<n>@pr` bookmarks until `jj git remote remove pr` drops them. Watch
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
