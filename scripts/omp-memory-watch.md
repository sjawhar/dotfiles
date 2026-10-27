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
`memory.stat`. Swap is off (`MemorySwapMax=0`, `SwapTotal 0`), so file cache is the only part of
`memory.current` the kernel can give back, and counting it would alert on a scope in no danger.

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

## What one run does

/proc and /sys reads only, no transcript reading in the decision, no model call, ~0.4 s:

1. Every `omp` process on this machine, with the cgroup each runs in (`/proc/<pid>/cgroup`).
2. The ceiling it is heading for: the smallest numeric `memory.max` from that cgroup up to the
   root. All `max` means the ceiling is the machine's RAM and the kill would be a global OOM.
3. The pressure: `memory.stat` for a capped scope, `/proc/<pid>/statm` for an uncapped session.
4. A crossing opens at 70% of the ceiling, alerts after the 60 s grace, and clears under 62%
   (hysteresis) or when the process ends.

The session id is read **from the process** — a session id on its command line, else the
`~/.omp/agent/sessions/**/<stamp>_<id>.jsonl` it holds open — and is resolved only when an alert
is about to be sent, which is what keeps a run to a handful of file reads. When it cannot be
established the alert says "session unknown" and goes to the INFRA lead alone; it never guesses.

## Who hears about it

Agent sessions only, never a person:

- **the session itself**, because it owns the shedding;
- the **INFRA lead** (`01a13c57-d826-7242-80fe-5f0670188b11`), who owns this detector;
- the **chief of staff** (`01a13dca-e979-7264-af32-f654ef57a6f5`) only on a session's **second**
  crossing, or when one crossing is still open **and still rising** 10 minutes after its alert.

Duplicates are dropped, so a session that is also one of those two gets one message.

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
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run`.

## Inputs

| Input | Source |
|---|---|
| omp processes and their cgroups | `/proc/*/comm`, `/proc/*/cgroup` |
| The ceiling | `memory.max` from the scope up to `/sys/fs/cgroup`, else `MemTotal` |
| The pressure | `memory.stat` (`anon`, `shmem`, `kernel`, `sock`), `memory.current`, `/proc/<pid>/statm` |
| Session id (alert only) | `/proc/<pid>/cmdline`, else `/proc/<pid>/fd` → `~/.omp/agent/sessions/**/*.jsonl` |
| Self-watch (alert only) | the command lines of the other processes in the scope, or of a process naming this pid |
| Listener | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Samples and open crossings | `/mnt/nvme/sami/tmp/omp-memory-watch/state.json` |

## Running it by hand

```sh
scripts/omp-memory-watch --dry-run --print-alert          # decide and print, send nothing
scripts/omp-memory-watch --fraction 0.70 --grace 60       # the unit's defaults
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
