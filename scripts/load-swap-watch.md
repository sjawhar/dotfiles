# load-swap-watch

What it watches, what it will not catch, and the measurements behind each threshold.

Armed by `installers/load-swap-watch.sh` as the `load-swap-watch` user timer
(`omp/load-swap-watch.{service,timer}`). It detects only: it kills nothing, stops no worker and
changes no limit.

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

## Each condition on its own

CPU, swap and memory are judged apart and said apart: each has its own crossing, grace, alert,
escalation and clear, and one message never names two of them. On 2026-10-30T01:32Z an
escalation from `ossv-agent-open` read **"STILL OUT OF CPU+SWAP"** while that box carried
**load 27 on 32 cores with `cpu some avg60` at 7%** — a working box. The swap half was true; the
CPU half had recovered half an hour earlier, and the old code named the union of everything the
crossing had ever been. A headline that overstates one half makes the next true alert cheaper
to dismiss, which is what six lanes on that same machine had already paid for the night before.

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

## What an alert says

One line of box state (load, runnable vs blocked, all three PSI figures, swap, MemAvailable),
then why it fired, then the consumers:

- the agent census — how many `omp` processes the box carries and what they hold, because on a
  lane box the dial that causes this is the number of concurrent workers, not any one of them;
- CPU by command and by process over a 10 s delta, never a single `ps` reading: every top-CPU
  pid in a snapshot taken at 23:52Z on the incident box was already gone a minute later, because
  the load was thousands of short-lived `git`, `python3` and `gzip` children;
- swap held, by command and by process;
- each process named by its lane worker, its agent session, its **working directory** and its
  **tmux pane**, all read from the process and never guessed.

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
a session on another machine. Both are read once per alert — one `tmux list-panes`, one Envoy
GET — and never on a quiet period.

It also says what the condition looks like from a pane, because the wrong reaction is the
expensive one: every agent still runs, still answers and still writes its transcript, so a lane
reads as frozen, and restarting it costs its turn and adds another working set to the same box.

## Where alerts go

The box-health lead's Envoy role (`notifications.role.lead-box-health`): the first alert, the
escalation when a crossing is still open 30 minutes after it (`--escalate`), and the clear.
Nobody holds the role? The SRE session (`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) hears it
instead. Agent sessions only, never a person; neither the chief of staff nor the INFRA lead
hears a box alert (Sami, 2026-10-31).

A machine whose box health belongs to one session sets `BOX_HEALTH_OWNER` to that session id in
the service's environment: every message then goes to that session alone, with no role publish —
this is how the OSS Verify lane VMs (`ossv-agent-*`) send their alerts to the fleet monitoring
owner instead of to this box's role holder. An invalid value stops the watch at start.

## What it will not catch

- **A box that is slow for another reason.** A hung mount, a saturated disk or a network stall
  shows up in `io some`, which the alert prints but does not fire on; `disk-fill-watch` owns the
  disk.
- **One session's own memory.** `omp-memory-watch` owns that; this watch sees the box.
- **A lane that is stuck rather than starved.** A worker waiting on a peer, a dead role holder
  or an empty queue looks identical from `/proc`. `envoy-role-holder-watch` covers the holder
  case; the rest is the lane lead's.
- **The last few seconds.** Its instrument is a 60-second average, so a 10-second spike is
  invisible by design.

## Running it by hand

```bash
load-swap-watch --consumers              # who is using the CPU and holding the swap, now
load-swap-watch --consumers --json       # the same, as one JSON object
load-swap-watch --dry-run --print-alert  # decide and print; send nothing, write no state
load-swap-watch --dry-run --print-alert --grace 0   # render the alert for a crossing that just opened
```

Turn it off with `systemctl --user disable --now load-swap-watch.timer`; nothing re-arms it
until someone runs the installer again.
