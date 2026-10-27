# omp-box-memory-guard

Keeps a single runaway agent session from taking the **box** down, and tells the sessions and
the INFRA lead over Envoy what it is doing. It is the acting half of a four-part guard that
`installers/omp-box-memory-guard.sh` installs; the other three parts are kernel and systemd
configuration, not code.

## Why it exists

sami-rocqperf (64 cores, 251.67 GiB RAM, no swap) wedged on 2026-10-27. From earlyoom's own
minute-by-minute journal and the kernel log:

| Time (UTC) | |
|---|---|
| 08:45:45 | MemAvailable 65,039 MiB (28.3%), swap 0 of 0 |
| 08:48:36 | 22,521 MiB (9.95%) — a fall of 42 GiB in 2 min 51 s |
| 08:48:40 | earlyoom SIGTERMs eleven processes it picked by RSS, at its 10% floor |
| 08:53:24 | the kernel's machine-wide OOM: 4,157 tasks dumped, 74 GiB resident, 60.5 GiB of it `python3` |
| 09:00 → | 95-99% **system** cpu, 0% idle: direct reclaim with nothing left to reclaim |
| 09:14:55 | a page-allocation failure inside ext4 under `shrink_slab` |
| 13:56 | rebooted |

Per-session `MemoryMax` scopes did not protect the box, for two reasons that both needed
measuring:

* their ceilings **sum to about four times the RAM** — 46 scopes at 24 GiB on a 251 GiB box;
* and almost nothing is in one. Measured 2026-10-27T14:57Z, `user-1000.slice` held 193 GiB, of
  which **156 GiB was one unlimited login scope** (`session-3.scope`, 1,463 processes: tmux
  panes and everything they start) and only 36 GiB the per-session scopes under `app.slice`.
  `shims/omp` creates a scope only when `OMP_MEMORY_MAX` is set, and nothing sets it by default.

## The four parts, in the order the kernel meets them

| | What | Where |
|---|---|---|
| 1 | **Swap**: 64 GiB file on the NVMe array, `vm.swappiness=10` | `installers/omp-box-memory-guard.sh`, `/etc/fstab` |
| 2 | **Reclaim earlier**: `vm.watermark_scale_factor=100`, `vm.min_free_kbytes` 0.4% of RAM | `devbox/sysctl-memory-guard.conf` → `/etc/sysctl.d/70-omp-memory-guard.conf` |
| 3 | **Bound the aggregate**: `MemoryHigh=82%`, `MemoryMax=92%` on `user-<uid>.slice` | `devbox/user-slice-memory-guard.conf` → `/etc/systemd/system/user-.slice.d/60-omp-memory-guard.conf` |
| 4 | **Shed one session**: this script | `omp/omp-box-memory-guard.service` (systemd `--user`) |

Below all four, agent-infra's earlyoom (`-m 10,5`, its own drop-in) is untouched and stays the
last-resort net. This guard is deliberately **above** it: shedding one whole session cleanly,
with the session told why, beats earlyoom picking the largest RSS it is allowed to kill.

### 1. Swap, and why a file rather than zram

zram buys capacity by compressing, and its pool lives in the RAM the box is short of. This
machine's scarce resource is RAM (251 GiB, all of it in use) and its spare resource is a 3.4 TB
NVMe array with 512 GiB free that sustains GB/s. 64 GiB of real swap costs **no RAM at all**,
and is more than the whole anonymous footprint of everything on the box that is not an agent
session.

**Swap does not let a capped session grow.** `shims/omp` starts a capped session with
`MemorySwapMax=0`, so its ceiling stays a ceiling. That is the right interaction and the shim
should keep it: a cap payable in swap would stop bounding the session's footprint and would
thrash the array instead of killing one lane. Swap is here for everything else — the login
scope's cold pages, idle services, and the reclaim headroom that makes `memory.high` a
slowdown instead of a stall. Without swap the kernel cannot reclaim anonymous memory at all,
which is exactly how 09:00Z became 95% system cpu.

### 3. The ceiling on the slice, and what it costs

`MemoryHigh=82%` (206 GiB here) **throttles**: over it the kernel reclaims inside the slice and
charges the reclaim to whichever task keeps allocating. Nothing is killed; sessions get slower.
82% is above the slice's measured steady state (180-193 GiB, about 72 GiB of it page cache the
kernel gives back for free) and below the 240 GiB peak it reached while the box was dying.

`MemoryMax=92%` (231.5 GiB) is the hard stop, 25 GiB above the throttle — about one minute at
the fastest sustained fall measured here (23 GiB/min), which is the time this guard needs to
shed (a 5 s cycle plus a 10 s SIGTERM grace). Past it the kernel OOM-kills **inside the slice**,
so the victim is one of the user's own processes and never journald, sshd, tailscaled, dockerd
or secretsd: 20 GiB stays outside the slice for them, against a measured steady `system.slice`
of 3.4 GiB (its own `MemoryMin=896M` from agent-infra is untouched).

## The thresholds, and where they come from

Every level is a **fraction of MemTotal**, so the unit is right on a machine with other RAM.
On this box:

| | Fraction | Here | What happens |
|---|---|---|---|
| WARN | 0.25 | 62.9 GiB | one Envoy message to the INFRA lead and the three largest sessions |
| SHED | 0.125 | 31.5 GiB | SIGTERM the largest session, SIGKILL what is left after 10 s |
| HARD | 0.08 | 20.1 GiB | no grace: SIGKILL at once, and again every cycle while it lasts |
| CLEAR | 0.32 | 80.5 GiB | one clear message; the incident is closed |

Measured over the 32 h to 2026-10-27T14:53Z, 1,911 one-minute samples from earlyoom's journal:

* MemAvailable p50 123.0 GiB, p10 63.1 GiB, p05 51.7 GiB.
* **Below 64 GiB: 208 samples.** Below 32 GiB: **55 samples, every one of them inside a runaway
  episode** (05:46, 07:41, 01:27, 01:29, 03:35-04:30 and the 08:44 wedge) — none in steady work.
  So SHED fires in the incidents and nowhere else.
* Fastest sustained fall **23.0 GiB/min** (138.1 → 22.6 GiB in 302 s, 2026-10-26T07:36:50Z);
  fastest single minute 54.6 GiB/min. From SHED that is 82 s of runway, and a cycle plus the
  grace plus the kill spends about 16 s of it.
* earlyoom SIGTERMs at 10% = 24.2 GiB, **below** SHED by 7.3 GiB, so this guard always acts
  first and earlyoom only sees a box this guard failed to save.

There is a second shed trigger that does not look at MemAvailable at all: the slice **over its
`memory.high`** with its own `full` pressure at or above 25% for two cycles. That is the
throttle biting and getting nowhere — without it, a slice pinned over `high` leaves every
session in it crawling and nobody decides anything.

## How a session is measured, and who gets shed

The attribution is **imported from `scripts/omp-memory-watch`**, not re-implemented: one
definition of what a session is, used by the detector and by the thing that kills. Two shapes:

* **a scope of its own** (`shims/omp` with `OMP_MEMORY_MAX`): the scope's unreclaimable memory,
  `anon + shmem + kernel + sock` — exactly what the memcg OOM killer compares with the ceiling;
* **no scope of its own** (the common shape here): the `omp` process **plus every descendant**,
  summed from `/proc/<pid>/statm`. The process alone undercounts: at the 2026-10-27T05:55:14Z
  kill omp held 14.9 GiB of the 24 GiB that hit the ceiling and its tool subprocesses held the
  rest.

The victim is the largest of those, and only that: never a process picked by RSS. A scoped
session is signalled through its cgroup (`cgroup.kill` for the escalation, which is atomic and
catches anything forked while the guard was deciding); an unscoped one through its `omp`
process first, so it can take its own children down, and then its whole tree.

The guard never sheds itself, never anything outside `/user.slice`, never a session under
`--min-victim` (1 GiB), and never more than one session per cycle or oftener than 20 s unless
the box is under the HARD level.

## Who hears about it

Agent sessions only, never a person, same as `omp-memory-watch`:

* **one WARN per incident** → the INFRA lead (`01a13c57-d826-7242-80fe-5f0670188b11`) and the
  three largest sessions;
* **one message per shed** → the session being shed and the INFRA lead;
* **one CLEAR per incident** → everyone who was told.

Each message carries the evidence it used (MemAvailable, swap, `/proc/pressure/memory`, the
slice's `memory.current/high/max/events/pressure`, the session ranking with how each was
measured) and what to do about it.

## One incident, one state file

`/mnt/nvme/sami/tmp/omp-box-memory-guard/state.json` (`$XDG_STATE_HOME` on a machine with no
`/mnt/nvme`) is the only memory, held under an exclusive `flock` for the whole run, written and
`fsync`ed before any message is sent. A hand run against it loses the lock and exits.

**A run on any other state file may not kill a real session.** That is what makes a proof run
safe on a live box: a test passes its own `--state` and its own `--session-comm`, and the guard
refuses to shed anything named `omp` for the rest of that run.

## Running it by hand

```sh
scripts/omp-box-memory-guard --once --dry-run --print-alert \
    --state /mnt/nvme/sami/tmp/memguard-proof/state.json      # decide and print, touch nothing
systemctl --user status omp-box-memory-guard.service          # the live one
journalctl --user -u omp-box-memory-guard -n 50               # heartbeats every 5 min, and every decision
systemctl --user disable --now omp-box-memory-guard.service   # stop it
```

`installers/omp-box-memory-guard.sh` installs all four parts and is idempotent;
`devbox/install.sh` runs it on every agent host.
