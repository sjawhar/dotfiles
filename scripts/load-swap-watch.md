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

## The rules, and why each number

- **`cpu some avg60 ≥ 50%`** (`--cpu-stall`). PSI, not load average: load counts uninterruptible
  sleep, so the same figure can mean a box short of CPU or one waiting on a disk, and the two
  have different fixes. `cpu some` counts only time a task was runnable and waiting for a CPU,
  which is the lane's lost minutes exactly. 50% means over half the last minute had something
  waiting. The incident sat at 80% for over an hour; a quiet hour on the same box reads under
  10%, and the box at 00:06Z after the heaviest job stopped read 6%.
- **Run queue per core ≥ 3** (`--queue-per-core`), only on a kernel without PSI. Coarser: it
  says how many tasks want a CPU, not how long any waited.
- **Swap ≥ 85% used** (`--swap-used`). Past that the kernel has little left to page out, so the
  next pressure lands on the OOM killer. The incident was at 99.75%.
- **Swap draining to zero inside 2 h** (`--horizon`, rate over `--rate-window`, 1 h of this
  watch's own samples). The same shape as `disk-fill-watch`'s time-to-full: a floor alone says
  "low" for hours without ever saying "about to land".
- **`MemAvailable` under 8 GiB** (`--mem-floor`). The kill zone a box reaches after swap is
  gone. It was not what fired on 2026-10-29 — 127 GiB was available, and `memory full avg60`
  was 0.2% — which is why memory alone is a poor detector here and CPU is the primary.
- **Grace 600 s** (`--grace`), period 2 min: an alert about 12 minutes after onset. A build
  burst does not hold 50% for ten minutes; the incident held 80% for seventy.
- **Clear at 0.7 of each rule** (`--clear-factor`), so a crossing never flaps on its threshold.

## What an alert says

One line of box state (load, runnable vs blocked, all three PSI figures, swap, MemAvailable),
then why it fired, then the consumers:

- the agent census — how many `omp` processes the box carries and what they hold, because on a
  lane box the dial that causes this is the number of concurrent workers, not any one of them;
- CPU by command and by process over a 10 s delta, never a single `ps` reading: every top-CPU
  pid in a snapshot taken at 23:52Z on the incident box was already gone a minute later, because
  the load was thousands of short-lived `git`, `python3` and `gzip` children;
- swap held, by command and by process;
- each process named by its lane worker and agent session, read from the process (its
  `OMP_SESSION_ID`, a session id on its command line, or the transcript it holds open — and for
  a lane, the worker name in its brief path or working directory), never guessed.

It also says what the condition looks like from a pane, because the wrong reaction is the
expensive one: every agent still runs, still answers and still writes its transcript, so a lane
reads as frozen, and restarting it costs its turn and adds another working set to the same box.

## Where alerts go

The box-health lead's Envoy role (`notifications.role.lead-box-health`), falling back to the
INFRA lead's session when nobody holds the role, and the chief of staff when a crossing is still
open 30 minutes after its alert (`--escalate`). Agent sessions only, never a person.

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
