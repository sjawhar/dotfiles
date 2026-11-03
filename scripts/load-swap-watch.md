# load-swap-watch

What it watches, what it will not catch, and the measurements behind each threshold.

Armed by `installers/load-swap-watch.sh` as the `load-swap-watch` user timer
(`omp/load-swap-watch.{service,timer}`). It detects only: it kills nothing, stops no worker and
changes no limit. Since 2026-11-03 it also watches two kernel stalls that stop a box from
stopping or starting agent boxes ([below](#two-kernel-stalls-writeback-switch-stall-and-umount-hung)).

## The incident it is built from

On 2026-10-29, six agent lanes on `ossv-agent-open` (`c-asan-rt`, `c-libc-str`, `g-ph-hreply`,
`c-omx-writer`, `c-asanb`, `h-cmd-b2`) were reported frozen from about 22:46Z, and the box was
read as hung. Measured on the box at 23:55–23:58Z:

| Reading | Value |
| --- | --- |
| Cores / load | 32 / 251, 185, 135 |
| `cpu some` avg10 / avg60 / avg300 | 80.5% / 80.1% / 74.1% |
| `io some` avg10, `memory full` avg10 | 23.5%, 0.23% |
| `procs_running` / `procs_blocked` (3 samples) | 55, 84, 104 / 13, 24, 13 |
| Swap | `/swapfile`, 200,820,180 of 201,326,588 KB used — **99.75%**, 515 MB free |
| `omp` processes | **126**, holding 108.5 GiB RSS and **180.3 GiB of swap (95% of all swap)** |
| Workers in `running` state | **115** (`ossv-worker list`) |
| CPU by command, 10 s delta | `omp` 15,614 of ~32,000 ticks (**≈49% of the box**), git 1,882, python3 755, tmux 421 |

Nothing was stuck. `tmux capture-pane` answered in 0.01 s on all six panes, each showing a live
idle prompt; no `tool_execution_start` in any of the six transcripts lacked its completion; and
every one of the six had been written to within the previous 90 seconds. The lanes were being
served a fraction of a core each, with their working sets in a swapfile with nothing left.

No instrument on the box watched either number. `disk-fill-watch` watches filesystem bytes;
`omp-memory-watch` watches one session against its own memcg ceiling and says nothing about a
box carrying a hundred of them. This watch is the missing one.

## Each condition on its own, in one message

CPU, swap and memory are judged apart: each has its own crossing, grace, alert, new-fact check
and clear, and **no line ever names two of them**. On 2026-10-30T01:32Z an escalation from
`ossv-agent-open` read **"STILL OUT OF CPU+SWAP"** while that box carried **load 27 on 32 cores
with `cpu some avg60` at 7%** — a working box. The swap half was true; the CPU half had recovered
half an hour earlier, and the old code named the union of everything the crossing had ever been.
A headline that overstates one half makes the next true alert cheaper to dismiss, which is what
six lanes on that same machine had already paid for the night before.

The verdicts of one run share **one message**, one line each, worst first:

```text
BOX SHORT OF CPU on sami-rocqperf: cpu some avg60 58% (rule 35%) at load 349/286/159 on 64 cores; 130 runnable, 12 blocked.
BOX OUT OF SWAP: 95.5% used, 5.0 GiB free of 112.0 GiB (rule 85%).
BOX SHORT OF MEMORY: MemAvailable 18.0 GiB under the 25.2 GiB floor.
DO (box-health owner): cut the concurrent agents here; move new work to another box; restart no agent; stop finished sessions by exact pid, owner asked first.
Full report: /mnt/nvme/sami/tmp/load-swap-watch/reports/20261103T030207Z-alert.txt
```

Separate messages were the other half of the same fault: on 2026-11-01T16:36Z this watch sent
`BOX SHORT OF CPU` and `BOX OUT OF SWAP` as two messages of 5.6 and 6 KB in the same minute, with
the same load line and the same consumer table in both.

## The rules, and why each number

- **`cpu some avg60 ≥ 35%` AND load over the core count** (`--cpu-stall`). Both, because either
  alone is wrong here. PSI alone called a box at 7% starved; load alone counts uninterruptible
  sleep, so load 221 may be a box waiting on a disk. The bar comes from the readings of
  2026-10-29/30, not from a round number: **healthy** — `ossv-agent-open` 7% at load 27/32,
  answering normally; `sami-leanperf` 0% at load 1/160. **Starved** — `ossv-agent-open` 80% at
  load 251/32 during the six-lane incident; `sami-rocqperf` 42–65% at load 204–310/64, its lanes
  visibly slow at the low end; `ossv-agent-open2` 55% at load 76/32. The lowest starved reading
  is 42% and the highest healthy one is 7%, so 35% sits in that empty band, below every starved
  reading with margin and five times the highest healthy one.

  **Not on every box.** `HOST_SETTINGS` in the script turns the CPU rule off per host, and it is
  off on `sami-leanperf`: a 160-vCPU Lean timing-benchmark box whose bursts hold every core by
  design (p95 92% of 160 vCPU over 1,055 ten-minute samples, peak 1-minute load 1,246 —
  [leanperf-load.md](dispatch://INFRA-993/artifact/leanperf-load-md)). Its 7 CPU messages in the
  48 h to 2026-11-03 (`cpu some avg60` 76–83% at load 195/160) drew no reaction. A higher bar
  would not help: at those peaks `cpu some` nears 100%, so any bar in the band fires too. Swap
  and memory still alert there — an OOM kill is nobody's plan. The setting is in git, where a
  reviewer sees it, not in a drop-in on one machine; `--cpu-rule on|off` overrides it by hand.
- **Run queue per core ≥ 3** (`--queue-per-core`), only on a kernel without PSI. Coarser: it
  says how many tasks want a CPU, not how long any waited.
- **Swap ≥ 85% used** (`--swap-used`), with **no CPU condition at all**. Zero free swap is worth
  saying on an idle box: it is a statement about the next memory squeeze, not about now, since
  from there the kernel's reclaim has nowhere to go but the OOM killer, and it does not choose
  the idle lane. `ossv-agent-open2` at 01:32Z had **0.0 GiB free with 144 GiB of RAM available**
  and 122 `omp` processes holding 179 GiB between them.
- **Swap draining to zero inside 2 h** (`--horizon`, rate over `--rate-window`, 1 h of this
  watch's own samples). The same shape as `disk-fill-watch`'s time-to-full: a floor alone says
  "low" for hours without ever saying "about to land".
- **`MemAvailable` under the box's floor** (`--mem-floor`, `--mem-floor-fraction`,
  `--mem-floor-cap`). The kill zone a box reaches after swap is gone. It was not what fired on
  2026-10-29 — 127 GiB was available, and `memory full avg60` was 0.2% — which is why memory
  alone is a poor detector here.

  The floor is `max(8 GiB, 10% of MemTotal)`, never above `25% of MemTotal`. One absolute
  number cannot serve every box: 8 GiB is 12.5% of a 64 GB lane VM, 3.2% of the 251 GB devbox
  and 0.5% of a 1.5 TB builder, so on the big boxes it is reached only once the box is already
  dying. The cap is the other half: a floor above a quarter of the box is not a floor but a
  permanent alert, and on 2026-10-31 at 03:49:22Z a hand run with `--mem-floor 200000000000`
  fired on `ossv-agent-repo`, a 64 GB box, saying "MemAvailable is 58.6 GiB, under the
  186.3 GiB floor". Every alert says which of the three produced the number it used, and a box
  whose MemTotal cannot be read keeps the absolute floor rather than a cap of zero.
- **Grace 600 s** (`--grace`), period 2 min: an alert about 12 minutes after onset. A build
  burst does not hold the bar for ten minutes; the incident held 80% for seventy.
- **Clear at 0.7 of each rule** (`--clear-factor`), per condition, so a crossing never flaps on
  its threshold — and CPU also clears the moment the load fits on the cores again.
- **A CPU crossing never clears while the box is stalled** (`--io-hold`, default 50): not while
  more tasks are blocked than the box has cores, and not while `io some avg60` is at or over the
  hold. `cpu some` counts tasks that are *runnable*; tasks stuck in uninterruptible sleep are
  neither runnable nor done, so a box waiting on its disk reads as a box with CPU to spare. Four
  of the five CPU clears sami-rocqperf sent in the 48 h to 2026-11-03 were that false reading:
  **2026-11-02T10:25Z "CLEARED … load 851/598/449 on 64 cores, 97 runnable, 723 blocked"**
  (`io some` 94%), 11-03T00:32Z (329 blocked, io 91%), 00:36Z (167 blocked, io 93%) and 01:39Z
  (io 68%; the crossing re-opened 8 minutes later). The one true clear, 11-01T12:45Z, had
  `io some` 6%, and the box was at load 8 by 14:20Z. 50% sits between the two groups.

## Two kernel stalls: WRITEBACK SWITCH STALL and UMOUNT HUNG

On 2026-11-02 and 2026-11-03 sami-devbox's cgroup writeback got stuck cleaning up a dead cgroup
(dispatch://INFRA-993). A bulk-copy unit that wrote millions of inodes ended, and the kernel had to
move each of its inodes to the parent cgroup's writeback. On these 7.0 kernels (7.0.0-1017-azure,
7.0.0-1014-gcp; the fix is torvalds f6988c90671e, in v7.3) `cleanup_offline_cgwb` re-walks every
inode it has already queued on each pass, so the move ran at 57-200 inodes/s and slowed. Every
`umount` and `fusermount3` then waited in `cgroup_writeback_umount` in D state for hours, agent
boxes could not stop or start (`agentbox new` failed at the sysbox-fs pre-register), and `sync()`
hung. Nothing alerted. Both conditions below are judged on every box this watch runs on; each has
its own crossing, alert, new fact and clear, in the same message as the others.

| Reading | sami-devbox in the stall | sami-devbox healthy | sami-rocqperf healthy |
| --- | --- | --- | --- |
| When | 11-03 12:45-13:24Z | 11-03 13:36-13:55Z, after its 13:33Z reset | 11-03 13:22-13:55Z |
| `writeback:inode_switch_wbs`, per 10 s count | 57-200/s for over 1 h (12:45-13:03Z); 201/s at 13:16Z | 0-867/s, median 0.1/s, mean 46/s, 8 of 90 counts ≥ 20/s | 0.7-778/s, median 6.9/s, mean 52/s, 24 of 81 counts ≥ 20/s |
| A `kworker/N:M+inode_switch_wbs` busy, of 60 one-second samples | 60 (13:22Z), at 55-98% CPU for 1 h 40 min | 0 (13:54Z) | 7 (13:22Z), 1 (13:55Z) |
| `umount` / `fusermount3` in D state | 9, blocked 6-77 min at 13:24Z (30 min to 3.4 h over the day) | 0 | 0 |

The rate counts are `sudo -n perf stat -x, -I 10000 -e writeback:inode_switch_wbs -a`; the busy
worker is a scan of `/proc/*/comm` once a second.

**WRITEBACK SWITCH STALL: `writeback:inode_switch_wbs` ≥ 20/s with a switch worker busy, in every
run for 10 minutes** (`--switch-rate`, `--grace`). The rate alone does not separate the two
states: a healthy box switches inodes in bursts, up to 867/s for 10 s, and sami-rocqperf's mean
over 15 minutes (52/s) is near the stall's lowest reading (57/s). What a burst never does is keep
a switch worker busy: the comm of a kworker ends in `+inode_switch_wbs` only while it runs a work
item of that queue, and that held in 60 of 60 seconds in the stall against at most 7 of 60 on
either healthy box. So a run counts only when both hold, and a crossing needs six runs in a row
(the 2-minute period over the 600 s grace). On a healthy box that is at most (7/60)^6, about one
window in 400,000. The rate bar is a third of the stall's lowest reading, so the alert still holds
as the move slows. It clears when a run reads the rate under 14/s (`--clear-factor` 0.7) or finds
no switch worker busy. Replaying sami-rocqperf's 81 counts as runs, with the worker busy in one
run in eight, sends nothing (`scripts/tests/test_load_swap_watch_stalls.py`).

How the rate is read. `perf stat` counts the tracepoint without a ring buffer or a trace file, and
changes no kernel setting; it needs root, so it runs under `sudo -n`, which fails at once instead
of waiting for a password. `sudo -n` works for `sami` on both boxes (checked 2026-11-03 13:16Z),
and both kernels have the tracepoint. One 5 s count (`--switch-sample`) by the watch's own
`switch_rate()` at 14:06-14:07Z took 9.8-16.3 s of wall time and 0.21-0.33 s of CPU on
sami-rocqperf at load ~300 on 64 cores, and 5.3 s and 0.14-0.16 s on sami-devbox. So it runs
only while a switch worker is busy or the crossing is open; on sami-rocqperf that is about one
run in eight. The walk of `/proc/*/comm` that finds the worker (and the hung umounts) runs every
time: 0.10 s of CPU over 4,200 processes on sami-rocqperf, 0.03 s on sami-devbox. A `perf` that
fails (no sudo, no tracepoint) is logged and never read as a rate of zero: it opens nothing and
clears nothing. A tracefs instance was not used: its counters are a histogram trigger, which is
kernel state that outlives the run. A worker's CPU share alone (`/proc/<pid>/stat`) would need no
root, but it does not count inodes; it is in the report instead.

**UMOUNT HUNG: an `umount` or `fusermount3` (by comm) in D state for over 5 minutes**
(`--umount-hung`), said at once, since the 5 minutes are the hold. The time is time blocked, not
process age: `/proc/uptime` minus `se.exec_start` of `/proc/<pid>/sched` (when the task last ran,
in ms since boot), as `devbox/sami-devbox-check` reads its D1 line. Those files and
`/proc/<pid>/cmdline` are world-readable, so this needs no root. A teardown umount on a healthy
box ends in seconds; in the stall they were blocked 30 minutes to 3.4 hours, and none was in D
state on either box at 13:24-13:55Z. The message names the longest-blocked task, its pid, its
mount point (the last argument of its cmdline that is not an option) and how long it has been
blocked; the report names every one. It clears when no such task has been blocked for 3.5 minutes.

**What the box-health owner does**, in every message about either, whole, whatever else the
message drops to stay under 600 characters:

```text
DO (box-health owner): reset by the reset plan sami-rocqperf:/mnt/nvme/sami/scratch/devbox-provision/reset-plan-20261103.md; never systemctl reboot (it hangs on this lock). Cause: a dead cgroup that wrote millions of inodes; stop such a copy only with devbox/bulk-copy/pull-unit.sh stop.
```

The report adds the plan's order (safe-point asks, `sync -f` of each live file system, the cloud
reset), that `shutdown` and plain `sync` hang too, and to kill nothing to free the box.

**Said again** only when the stall's own figure doubled past the highest it read while open: the
switch rate (it swings 57-200/s inside one stall, so a mark taken from the first message alone
would re-send on the swing), or the number of hung tasks. A task blocked longer is time passing,
not a fact. A stall message has no consumer table, so it does not pay for the 10 s CPU sample.

Why a rate held for 10 minutes, not a count: a cgroup that ends on a healthy box switches its
inodes in one burst that drains at once. bpftrace on `writeback:inode_switch_wbs` per
`old_cgroup_ino` on sami-devbox, 2026-11-03 13:53-14:07Z, after the reset with 6 boxes running
(DevboxProvision.Unwedge): one cgroup end switched 275,480 inodes, an 86k-file tar test 72,382,
four others 4,338 each, the rest under 3,000. The stall was one old cgroup at 57-200/s for over
an hour.

## What the message says, and what the report holds

**The message is at most 600 characters** and ends with `Full report: <path>`. Its first ~200
characters are the decision — what, where, how bad — then what the box-health owner does, then
the pressures and the top consumer of each condition that spoke. It is read in a notification,
where nothing below the first lines is seen; the messages it replaces were **4.9–6 KB**, of
which the first 200 characters already carried the decision.

Everything else goes in the report, written to `<state dir>/reports/<UTC stamp>-<kind>.txt`
(so `/mnt/nvme/sami/tmp/load-swap-watch/reports/` here), with reports older than 7 days deleted
in the same run:

- the full box state line and the full reason each verdict fired;
- when the crossing opened, when it was first said, and the worst figures already said;
- what the condition looks like from a pane and what to do about it, per condition;
- the agent census — how many `omp` processes the box carries and what they hold, because on a
  lane box the dial that causes this is the number of concurrent workers, not any one of them;
- CPU by command and by process over a 10 s delta, never a single `ps` reading: every top-CPU
  pid in a snapshot taken at 23:52Z on the incident box was already gone a minute later, because
  the load was thousands of short-lived `git`, `python3` and `gzip` children;
- swap held and RSS held, by command and by process;
- each process named by its lane worker, its agent session, its **working directory** and its
  **tmux pane**, all read from the process and never guessed.

A report that cannot be written (a full disk is one reason a box is in trouble) never stops the
message: the message goes, and names the report as not written and why.

## Said again only on a new fact

While a crossing stays open, the watch speaks again **only when the box is clearly worse than
the worst it has already said** about that crossing:

- load, or tasks blocked past the core count, at least **doubled**;
- swap free, or `MemAvailable`, at least **halved** (and by at least 1% of the total, so a figure
  already near zero cannot halve its way into a message every run);
- a **command newly at the top** of that condition's consumers, looked for every `--recheck`
  (30 min) and never more often, because finding it costs a 10-second CPU sample;
- for a kernel stall, its switch rate or its count of hung umounts at least **doubled** past the
  highest it read while open.

Never because time passed. The old rule sent a `STILL …` at 30 minutes by the clock: of the four
it sent in the 48 h to 2026-11-03, the SRE acted on none. The marks ratchet, so a level already
reported is not news again: on 2026-11-02 "369 blocked" was said at 00:20Z, the load doubled at
08:10Z while 67 were blocked, and the old code then called 182 blocked at 09:16Z a new fact.

### Which session, and how it is checked

`OMP_SESSION_ID` is **inherited**. A session that starts a lane box's supervisor over ssh
stamps its own id on the tmux server and on every process below it for the life of the box, and
a session id in an `omp --resume <path>` is the id its session *directory* was created with,
which a resume replaces. On 2026-10-30 at 20:17:23Z this watch named 39 `python3` processes on
`ossv-agent-repo` as `01a14b36` — VERIFY, which runs on sami-rocqperf — when the live owner was
the repo lane `01a14b84`.

So a session id is now taken in this order, strongest first, and then **verified**:

1. the lane worker's own `.ossv-workers/<name>/session`, which `ossv-worker` rewrites on every
   resume (and which is ignored while `session_unconfirmed` sits beside it, because until then
   it still names the previous session);
2. the nearest ancestor `omp`'s own resume path or the transcript it holds open;
3. the environment, last and never first.

The id is then checked against `GET /v1/sessions` on Envoy, filtered to this box's host. One
that is not live here reads as `session X from the process environment (started it) (not live
on this box); the live owner on this box is Y`, where Y comes from the worker's session file,
or from the box having exactly one live session. An Envoy that does not answer leaves every
id unverified and named as before: a lookup failure must never read as "nobody is live".

The working directory and the pane are there for the same reason: neither can be inherited from
a session on another machine. Both are read once per report — one `tmux list-panes`, one Envoy
GET — and never on a quiet period.

The report also says what the condition looks like from a pane, because the wrong reaction is
the expensive one: every agent still runs, still answers and still writes its transcript, so a
lane reads as frozen, and restarting it costs its turn and adds another working set to the box.

## Where alerts go

The box-health lead's Envoy role (`notifications.role.lead-box-health`): the alert, a new fact
while the crossing stays open, and the clear — one message per run, whichever of them it carries.
Nobody holds the role? The SRE session (`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) hears it
instead. Agent sessions only, never a person; neither the chief of staff nor the INFRA lead
hears a box alert (Sami, 2026-10-31).

A machine whose box health belongs to one session sets `BOX_HEALTH_OWNER` to that session id in
the service's environment: every message then goes to that session alone, with no role publish —
this is how the OSS Verify lane VMs (`ossv-agent-*`) send their alerts to the fleet monitoring
owner instead of to this box's role holder. An invalid value stops the watch at start.

## What it will not catch, and what these rules give up

- **A box that is slow for another reason.** A saturated disk or a network stall shows up in
  `io some`, which the message prints but does not fire on; `disk-fill-watch` owns
  the disk. Since 2026-11-03 a high `io some` also holds a CPU crossing open, so a box that is
  IO-bound keeps a CPU crossing it would once have cleared out of: that crossing is reported
  once, not once per flap, and its clear waits for the stall to end.
- **CPU on `sami-leanperf`.** Nothing there says the benchmark box is saturated; its swap and
  memory rules still speak. A CPU shortage on that box has to be read from its own run.
- **A crossing that stays bad without getting worse.** It is said once. A reader who wants the
  current state runs `load-swap-watch --consumers`, and every run is in
  `journalctl --user -u load-swap-watch`.
- **One session's own memory.** `omp-memory-watch` owns that; this watch sees the box.
- **A lane that is stuck rather than starved.** A worker waiting on a peer, a dead role holder
  or an empty queue looks identical from `/proc`. `envoy-role-holder-watch` covers the holder
  case; the rest is the lane lead's.
- **The last few seconds.** Its instrument is a 60-second average, so a 10-second spike is
  invisible by design.
- **A writeback stall that has slowed under 14/s**, or whose switch worker is idle at a run. It
  clears; the umounts it holds still say UMOUNT HUNG. A stall the kernel has not yet made a
  teardown wait on is said only by its rate.
- **A writeback rate the box cannot read.** Without `sudo -n perf` the rule never opens; each run
  logs `writeback switch rate not read: …` in its journal. UMOUNT HUNG needs no root.
- **A hung mount other than a teardown.** Only `umount` and `fusermount3` are judged; any other
  task in D state (an `rsync` on a hung NFS mount) is not this condition.

## How much it says

Measured by replaying this box's own journal state lines (2026-11-01T09:50Z → 2026-11-03T02:52Z,
947 samples, 41 h) through both versions: **19 messages before, 12 after** — 11/day to 7/day on
sami-rocqperf, and 0 instead of ~3.5/day on sami-leanperf. The longest message a run can produce
(three verdicts at once) is 581 characters; the old alerts were 4.9–6 KB.

## Running it by hand

```bash
load-swap-watch --consumers              # who is using the CPU and holding the swap, now
load-swap-watch --consumers --json       # the same, as one JSON object
load-swap-watch --dry-run --print-alert  # decide; print the message and the report it would write
load-swap-watch --dry-run --print-alert --grace 0   # the same for a crossing that just opened
load-swap-watch --dry-run --print-alert --grace 0 --switch-sample 10   # a longer switch-rate count
load-swap-watch --dry-run --print-alert --grace 0 --cpu-rule off   # as sami-leanperf judges it
```

Turn it off with `systemctl --user disable --now load-swap-watch.timer`; nothing re-arms it
until someone runs the installer again.
