# session-cpu-record

Who used this box's CPU, and when — recorded every 5 minutes, kept 7 days, readable afterwards.

## Why it exists

On 2026-11-01 a speed tick asked which omp sessions on sami-rocqperf had used the 64 cores
overnight. The answer was "not measurable". No instrument kept the number:

- sysstat runs here with `SADC_OPTIONS="-S DISK"`, so `sar` records disks, not processes.
- There is no atop.
- `load-swap-watch` measures per-process CPU, but only inside an alert it sends over Envoy, and
  it writes none of it down.

Per-process CPU can only be attributed while the processes are alive: an agent session's CPU is
almost all in short-lived tool children, and the question is always asked later. So it has to be
written down as it happens.

## What one sample records

One JSON object per sample, appended to `/mnt/nvme/sami/tmp/session-cpu/<UTC date>.jsonl`
(`$XDG_STATE_HOME/session-cpu/` on a machine without that disk):

```json
{"at": "2026-11-01T16:48:53Z", "epoch": 1793..., "interval_s": 73.1, "host": "sami-rocqperf",
 "cores": 64,
 "sessions": [{"session": "01a13bcd-…", "cpu_seconds": 295.4, "rss_bytes": 10880401408,
               "procs": 3, "source": "cgroup", "cwd": "/home/sami/src/…", "title": "🚰 PIPE: …"}],
 "counters": {"__at__": 1793..., "cg:/user.slice/…/run-r….scope": 44946.8}}
```

`cpu_seconds` is CPU time used **in that interval**, not since the session started. `counters`
holds the cumulative readings the next sample subtracts from; it is this tool's state, and it
lives in the records rather than in a separate state file so a sample is self-contained.

## Attribution, and the two sources of CPU time

Process-to-session attribution is `session_claim` of `scripts/load-swap-watch`, imported, not
copied: a process's own omp (its `--resume` path, or the transcript it holds open), then the
lane worker's session file, and the environment last, because `OMP_SESSION_ID` is inherited and
names whoever started the ancestor. A process whose ancestry names no session is recorded under
`unknown` rather than guessed at.

CPU time comes from the session's cgroup where that is sound, and from `/proc` otherwise:

| `source` | what is read | what it counts |
| --- | --- | --- |
| `cgroup` | `cpu.stat usage_usec` of the scope, and `memory.current` | every descendant, including the thousands of tool children that have already exited |
| `proc` | `utime+stime+cutime+cstime` of the session's own agent processes | the agent, plus each child it has already reaped |

The cgroup is used only when **exactly one** session id is claimed inside it. A scope is not
always one session: measured on sami-rocqperf on 2026-11-01, 121 omp processes sat in 84
cgroups, and the ssh login scope `session-c5.scope` held a `-p` headless run in
`/home/sami/src/bet-grade` beside a `--resume` session in `/home/sami/src/oss-verifier`. A
`proc` row undercounts a child that is still running — its time lands in the sample after it
exits — which is why the cgroup is preferred wherever it is unambiguous.

A counter seen for the first time contributes its whole total when the thing it counts started
after the previous sample, and nothing when it was already running: most of an old counter's
total was burned before the window, and charging it to one 5-minute interval would invent a
spike.

## Reading it back

```
session-cpu-record top --since 15m                   # the last quarter of an hour
session-cpu-record top --since 2026-11-01T12:00Z --until 2026-11-01T18:00Z -n 20
session-cpu-record top --since 6h --json             # the same ranking as JSON
```

`--since`/`--until` take an ISO instant (`2026-11-01T12:00Z`) or an age (`15m`, `2h`, `3d`).
`CORES` is the session's CPU-seconds divided by the wall time the samples cover, so it reads
like `top`'s load share: 4.04 means the session held four cores for the window. The last row is
every session, which says how much of the box the agents were using in total.

```
 CORES   CPU-H      RSS  SESSION                               TITLE                 CWD
  4.04    0.08    10.1G  01a13bcd-3e9f-7770-b0f4-88fa364eb58e  🚰 PIPE: task pipeli… /home/sami/src/<task repo>
 46.70    0.95           all 77 session(s)
```

Titles and directories come from one Envoy `GET /v1/sessions` per sample; a session Envoy does
not know still gets the working directory of one of its processes.

## Cost, and what it does not do

One sample costs about **0.5 CPU-seconds** and ~18 s of wall clock on sami-rocqperf at load 300
with ~3,000 processes and ~120 agent processes (most of the wall clock is the Envoy GET). Only
`/proc/<pid>/comm` is read for every pid; the expensive attribution runs on agent processes
alone. At one sample per 5 minutes that is 0.003% of a 64-core box.

It records. It alerts nobody, kills nothing and changes no limit — `load-swap-watch` is the
detector, and this is the ledger it never kept.

## Turning it off

```
systemctl --user disable --now session-cpu-record.timer
```

The records stay where they are and age out within 7 days; the tool deletes a day file older
than that on every sample.
