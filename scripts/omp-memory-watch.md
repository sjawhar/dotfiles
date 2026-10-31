# omp-memory-watch

Every 30 s, checks that no agent session on **this** machine is heading for the memory ceiling
its `omp` process runs under, and alerts the session itself over Envoy while it can still
checkpoint and shed work on its own terms.

## Why it exists

On 2026-10-27T05:55:14Z the kernel OOM-killed the Verified Linux owner session inside its own
systemd scope. The kernel's record (`journalctl -k`):

```
Memory cgroup stats for /user.slice/user-1000.slice/user@1000.service/app.slice/run-u174595.scope:
memory: usage 25165824kB, limit 25165824kB, failcnt 121421
swap: usage 0kB, limit 0kB, failcnt 0
oom-kill:constraint=CONSTRAINT_MEMCG,...,task=omp,pid=3398496,uid=1000
Memory cgroup out of memory: Killed process 3398496 (omp) total-vm:177177208kB,
  anon-rss:15522968kB, file-rss:100032kB, shmem-rss:0kB
oom_reaper: reaped process 3398496 (omp)
```

About 40 in-session subagent lanes died with it and nothing warned first. (dispatch://INFRA-602)

**Prevention belongs to the session, detection to this watch.** A session's own self-watch warns
first and the session sheds its own lanes; this watch sits above the self-watches, kills nothing,
sheds nothing and restarts nothing.

## What it measures, and why not RSS

`shims/omp` starts each session under `systemd-run --user --scope -p MemoryMax=$OMP_MEMORY_MAX
-p MemorySwapMax=0`, so the **scope** is the session's whole budget — its tool subprocesses
included — and the scope is what the memcg OOM killer compares with the ceiling. The omp
process's RSS is not:

| At the 05:55:14Z kill | |
|---|---|
| The scope's ceiling | 24 GiB (`memory.max` of `run-u174595.scope`; every ancestor is `max`) |
| The scope at the kill | 24 GiB, `failcnt 121421` |
| The `omp` process | 14.9 GiB resident — **62%** of what hit the ceiling |
| 20 `python3` tool subprocesses in the same scope | 13.1 GiB resident between them |

So the signal is the scope's **unreclaimable** memory, `anon + shmem + kernel + sock` from
`memory.stat`. In a scope `shims/omp` starts, swap is off (`MemorySwapMax=0`), so file cache is
the only part of `memory.current` the kernel can give back, and counting it would alert on a
scope in no danger. A scope `ossv-worker` starts sets `MemoryMax` alone, so its
`memory.swap.max` is `max` and its anonymous memory **can** page out: there the unreclaimable
number can stay low while the scope still reaches its ceiling, which is what the wall trigger
below exists for. Every alert reads `memory.swap.max` per scope and says which shape it is; it
used to assert `MemorySwapMax=0` for all of them, which was false for every worker scope.

A session with **no scope of its own** (started outside `shims/omp`, so it lives in the login
session's scope — `session-c45.scope` here, 744 processes and 51 GiB of other sessions' work)
is measured on its own process RSS instead, against the machine's RAM. Charging a shared login
scope to one session would alert on every session in the login.

## The threshold

**70% of the scope's own ceiling**, which is **16.8 GiB of 24 GiB** for a default session, with
**7.2 GiB of headroom**. Measured on this box on 2026-10-27, 159 scopes sampled every 30 s for
13.7 min:

- Of the 12 scopes that reached 5 GiB, the fastest **sustained** rise (best 5-minute window) was
  0.99 GiB/min and the median was 0.48 GiB/min. One-minute bursts reach 3.5 GiB/min but do not
  sustain, which is what the 60 s grace is for.
- Detection costs at most 90 s (a 30 s period plus the 60 s grace) = 1.5 GiB at the fastest
  sustained rise. **5.7 GiB is left: 5.8 minutes at 0.99 GiB/min, 12 minutes at the median.**
- That is the shed budget. Re-spawning 11 lanes took the Verified Linux lane 6 minutes after the
  kill (~33 s a lane), and writing handover files is seconds, so minutes is the right order.
- 75% would leave 4.5 minutes after detection at the fastest sustained rise; 60% (14.4 GiB) is
  inside the range healthy sessions reach — one scope peaked at 13.2 GiB during the sample.
- No healthy scope came near 16.8 GiB in that window (the highest peak was 13.2 GiB), so the
  measured false-positive rate is zero.

**A fixed RSS number does not work on this box.** The live ceilings are 24 GiB (46 sessions),
none at all (15), 4 GiB (5), 12 GiB (2), 48 GiB (1) and 16 GiB (1). A 12 GiB threshold is above
the entire ceiling of the 4 GiB scopes and at the ceiling of the 12 GiB ones — it would warn
after the kill — while on the 48 GiB scope it would fire at 25%.

**It sits above the session self-watches.** The known self-watch
(`/mnt/nvme/sami/tmp/verified-linux/handover/ol-rss-watch.sh <pid> 10`) warns at 10 GiB of omp
RSS. Where the growth is in the omp process — the only thing that self-watch can see — 16.8 GiB
of scope pressure is reached only after RSS passes 10 GiB, so the session's own watch always
speaks first. Where the growth is in the session's **tool subprocesses**, the self-watch is blind
to it and this watch is the only warning, which is why every alert says whether a self-watch was
found and never implies there is none when it cannot tell.

An uncapped session alerts at a flat `--unlimited-warn` (16.8 GiB of its own RSS): the level a
capped session is warned at. A fraction of 251 GiB of RAM is a number no single session reaches.

## The second trigger: a scope that REACHES its wall

The 70%-for-60 s crossing is a *forecast*, and it needs the pressure to be both unreclaimable
and held. A scope can spend its whole budget and meet neither test. Measured on
**ossv-agent-open on 2026-10-30 at 19:37Z**: of 95 worker scopes, **31 had touched their 24 GiB
wall** (`memory.events` `max` above zero, up to 42 422 times for one scope), while the watch
logged `0 close enough to sample` — not one of them was even above the 45% sampling level on
unreclaimable memory. Four had ever been reported.

Two reasons, both real on these VMs:

- **The pressure is reclaimable.** `ossv-worker` starts each worker with `systemd-run --user
  --scope -p MemoryMax=24G` and **no** `MemorySwapMax` (`bin/ossv-worker:577-578`), so
  `memory.swap.max` reads `max`: anonymous memory pages out, page cache fills the scope, and
  `anon+shmem+kernel+sock` stays low while `memory.current` sits on the ceiling.
- **The touch is shorter than the grace.** A scope that reaches the ceiling and falls back
  inside one 30 s period leaves no trace in any level-and-hold test.

So a capped scope is also reported when, since the last poll, either

- the **`max` counter of its `memory.events`** rises — the kernel refused an allocation at
  `memory.max` and had to reclaim to satisfy it — or
- its **`memory.peak`** rises to `--wall-fraction` (default 0.98) of the ceiling.

No hold, no grace, no rate. One report per scope per `--wall-cooldown` (default 15 min); touches
inside the cooldown are counted and the next report says how many it covers. The report names
the scope, the session, the `ossv-worker` name, `memory.peak`, `memory.current`, the
unreclaimable number, the `oom`/`oom_kill` counters, the **`memory.stat` split** (`anon`,
`file`, `shmem`, `kernel`, `sock`, largest first), the **process count and the RSS sum of every
process in the scope**, and the **three largest by RSS with their command lines**.

### What the owner is told to do

The three largest processes explained only 10–29% of a scope at its ceiling in the open4 and
open6 digests of 2026-10-30, so "find the process" was the wrong instruction for those scopes.
The advice line is chosen from the numbers:

| Condition | Advice |
|---|---|
| the named processes hold ≥ 60% of the peak | **one runaway**: find that process and stop or bound it; shedding lanes will not help |
| every process together holds ≥ 70% of the peak | **many processes**: reduce the fan-out — fewer lanes, fewer tool subprocesses at once |
| neither | **not in any process**: name the dominant `memory.stat` field and what it is (page cache, shmem/tmpfs, kernel slab), and point at the file traffic rather than the lane count |

In the third case, when `memory.current` has already fallen under half the peak, the advice
also says the peak was a burst and nothing on the scope now holds that memory.

The worker name and its session come from the scope's own command lines: `ossv-worker` starts
`omp` with `@<root>/.ossv-workers/<name>/message-*.md`, and `<root>/.ossv-workers/<name>/session`
holds the session id — used only when `session_unconfirmed` is absent beside it, because the
worker's own rule is that the file still names the *previous* session until then. Failing that,
the session is resolved from the process as before.

### The session a wall report goes to is verified

A session id taken from an `omp --resume <path>` is the id its session *directory* was created
with, and a resume replaces it; `OMP_SESSION_ID` is inherited and names whoever started the
box. Either one sent to Envoy reaches whoever holds that id somewhere else, not a silent owner:
on `ossv-agent-repo` on 2026-10-30 that stale id was VERIFY, which runs on sami-rocqperf.

So before a wall report is written the claimed id is checked against `GET /v1/sessions`,
filtered to this box's host. One that is not live here is replaced by the live owner — the
worker's `session` file, else the box's one live session — and the report says the swap
happened. When no live owner can be named, nobody is told directly and the lead's digest says
`session unknown`. An Envoy that does not answer leaves the id unverified and the report goes
where it would have gone: a lookup failure must never read as "nobody is live". The lookup
happens only when a touch exists, never on a quiet period.

**The first run after this trigger arrives seeds and reports nothing.** A counter already above
zero dates from an unknown moment in the past, often before the trigger existed, and reporting
31 scopes' history at once would bury their owners. A scope that appears while the state file
already holds wall memory is genuinely new, so its baseline is zero and its first real touch is
reported.

## What one run does

/proc and /sys reads only, no transcript reading in the decision, no model call, ~0.4 s:

1. Every `omp` process on this machine, with the cgroup each runs in (`/proc/<pid>/cgroup`).
2. The ceiling it is heading for: the smallest numeric `memory.max` from that cgroup up to the
   root. All `max` means the ceiling is the machine's RAM and the kill would be a global OOM.
3. The pressure: `memory.stat` for a capped scope, `/proc/<pid>/statm` for an uncapped session.
4. A crossing opens at 70% of the ceiling, alerts after the 60 s grace, and clears under 62%
   (hysteresis) or when the process ends.
5. Independently, a capped scope's `memory.events` and `memory.peak` are compared with the last
   poll's: a rise is a wall touch and is reported at once, with no hold.

The session id is read **from the process** — a session id on its command line, else the
`~/.omp/agent/sessions/**/<stamp>_<id>.jsonl` it holds open — and is resolved only when an alert
is about to be sent, which is what keeps a run to a handful of file reads. When it cannot be
established the alert says "session unknown" and goes to the box-health owner alone; it never guesses.

## Who hears about it

Agent sessions only, never a person:

- **the session itself**, because it owns the shedding;
- the **box-health owner** — the holder of the `lead-box-health` Envoy role
  (`notifications.role.lead-box-health`), and the SRE session
  (`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) when nobody holds it — on every alert, and again on
  the escalation, when a crossing is still open **and still rising** 10 minutes after its alert.

Neither the chief of staff nor the INFRA lead hears a box alert (Sami, 2026-10-31): the INFRA
lead owned this detector and handed box health to the SRE.

Duplicates are dropped, so a session that is also one of those recipients gets one message.

A **wall touch** goes to the scope's own session — the owner, who does the shedding — with the
whole report. The box-health owner gets **one batched digest per `--wall-digest` window**
(default 15 min) naming every scope that touched in it, instead of one message per scope: a box
can have tens of scopes at the ceiling at once (31 of 95 on ossv-agent-open on 2026-10-30), and
a message each would bury that session rather than inform it. Touches accumulate in the state
file across runs, so a quiet digest window loses none. Each scope's line in the digest carries
the **process count, the RSS sum, the `memory.stat` split, the chosen advice and the same three
largest processes with their command lines** under it: the owner reads the digest without the
scope owner's report beside it, and a top-3 list alone sent it hunting a process that did not
exist (the INFRA lead, 2026-10-30, when it owned this detector).

Each alert names the session, the pid and its RSS, the scope, the unreclaimable memory, the
ceiling and where it comes from, the percentage and the headroom left, the measured rise rate
and the minutes that headroom buys, how much of the pressure is omp and how much is its tool
subprocesses, whether a self-watch was found, and what to do: write handover files, stop
spawning lanes, shed or finish the lanes you can, restart on your own terms.

## One alert per crossing

`/mnt/nvme/sami/tmp/omp-memory-watch/state.json` is the only memory, and the service owns it:

- held under an exclusive `flock` for the whole run, so a hand run and the timer can never both
  open the same crossing; the loser logs `another run holds …` and skips its period;
- keyed by the scope (a capped session) or scope + pid (an uncapped one), with the pid and its
  `/proc/<pid>/stat` start time, so a recycled pid starts a new crossing;
- `alerted_at` is written and `fsync`ed **before** the first message is sent;
- the **wall memory** (`walls`) is keyed by the scope name alone and never by pid: one scope
  holds several `omp` processes (the session and its js-eval helper), and a flip between them
  must not look like a new scope and report the whole backlog again. A key is dropped when its
  scope is gone, and `systemd-run --scope` never reuses a name;
- `wall_pending` holds the touches waiting for the INFRA lead's next digest, newest last, at
  most `--wall-pending-max` (400);
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run`.

## Inputs

| Input | Source |
|---|---|
| omp processes and their cgroups | `/proc/*/comm`, `/proc/*/cgroup` |
| The ceiling | `memory.max` from the scope up to `/sys/fs/cgroup`, else `MemTotal` |
| The pressure | `memory.stat` (`anon`, `shmem`, `kernel`, `sock`), `memory.current`, `/proc/<pid>/statm` |
| The wall | `memory.events` (`max`, `oom`, `oom_kill`), `memory.peak`, `memory.swap.max` |
| Session id (alert only) | `/proc/<pid>/cmdline`, else `/proc/<pid>/fd` → `~/.omp/agent/sessions/**/*.jsonl` |
| Worker name and session (wall report only) | `.ossv-workers/<name>/` on a scope process's command line, and `<that dir>/session` unless `session_unconfirmed` is beside it |
| Top processes (wall report only) | `cgroup.procs`, `/proc/<pid>/statm`, `/proc/<pid>/cmdline` |
| Self-watch (alert only) | the command lines of the other processes in the scope, or of a process naming this pid |
| Listener | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Samples, open crossings, wall memory | `/mnt/nvme/sami/tmp/omp-memory-watch/state.json` |

## Running it by hand

```sh
scripts/omp-memory-watch --dry-run --print-alert          # decide and print, send nothing
scripts/omp-memory-watch --fraction 0.70 --grace 60       # the unit's crossing defaults
scripts/omp-memory-watch --wall-fraction 0.98 --wall-cooldown 900 --wall-top 3   # wall defaults

# The tests: synthetic cgroup trees, no real /sys read, python3 only.
python3 -m unittest discover -s scripts/tests -p 'test_omp_memory_watch_wall.py'
```

## Turning it off

```sh
systemctl --user disable --now omp-memory-watch.timer   # stop it
systemctl --user enable  --now omp-memory-watch.timer   # start it again
systemctl --user list-timers omp-memory-watch.timer     # when it next runs
journalctl --user -u omp-memory-watch.service -n 50     # what the last runs decided
```

`installers/omp-memory-watch.sh` arms it (links `omp/omp-memory-watch.{service,timer}` into
`~/.config/systemd/user`). Deleting the state file forgets every open crossing: the next run
re-learns and re-alerts on one that is still over the threshold.
