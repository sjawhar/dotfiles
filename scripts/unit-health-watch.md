# unit-health-watch

Says when a timer-driven systemd user unit on this box stops doing its job: its runs do not
complete, they are killed at their time limit, its timer stopped firing, or one run never ends.
It only reports. It restarts nothing and changes no unit.

Armed by `devbox/install.sh` as the `unit-health-watch` user timer
(`devbox/unit-health-watch.{service,timer}`), every 5 minutes.

```sh
unit-health-watch --report                          # every timer's reading now
unit-health-watch --report --at 2026-11-02T11:00Z   # the hour that ended then, from the journal
unit-health-watch --report --json
unit-health-watch --dry-run --print-alert           # decide and print; send nothing, write no state
```

## The incident it is built from

From 2026-11-01 16:00Z to 2026-11-02 13:49Z, systemd killed runs of
`envoy-role-holder-watch` at the unit's 25 s `TimeoutStartSec` while its timer fired every 30
seconds (fixed in dotfiles 2ec571e6 and f7359dd7): between 26% and 98% of each hour's runs, and
from 2026-11-02 10:00Z to 13:00Z not one run completed. Nobody saw it: the only witness was the
journal of the unit that had gone quiet. A role-holder alert came 7.5 hours late and a lane
stayed down for 37 minutes. The same unit's healthy hour, 2026-11-01 13:00Z, holds 119 runs
started and 119 completed.

`unit-health-watch --report --at 2026-11-02T11:00Z` reads the 10:00Z hour from this box's journal:

```
service                               every window start  done  kill  fail  run  ...  verdict
envoy-role-holder-watch.service         30s  60min   118     0   115     2    1  ...  NO RUN COMPLETED; RUNS KILLED AT THE TIME LIMIT
omp-memory-watch.service                30s  60min   118   113     5     0    0  ...  ok
```

(The 118th run started at 10:59:51Z and was killed after the hour closed, so it reads as still
running. By systemd's timestamps of the kill, the hour holds 116 killed runs and 2 that failed on
their exit code.)

## What one run does

1. **The list comes from the timers**: `systemctl --user list-timers --all`, plus every enabled
   timer unit file (which catches an enabled timer that was stopped or never loaded). No unit is
   named in this script, so a new timer is covered the moment it is loaded.
2. **The period comes from the timer**: `OnUnitActiveSec` or `OnUnitInactiveSec`, or the shortest
   gap between three elapses of `OnCalendar` (`systemd-analyze calendar --iterations=3`). A timer
   with only `OnActiveSec`, `OnBootSec` or `OnStartupSec` fires once; it is listed and not judged.
3. **The counts come from systemd's own records about the unit** (`USER_UNIT=`), never from what
   the unit prints, in one `journalctl` read for every unit: one hour of them is about a thousand
   records and 0.1 s here. The units' own output over the same two days is 200,000 records and
   44 s, so the unit's own line is read only for a unit being reported. One run is one invocation
   id:

   | Count | journal record |
   | --- | --- |
   | started | `Starting X...` |
   | completed | `Finished X.` (`JOB_RESULT=done`) of a oneshot; any other type once it ended without a failure |
   | killed at the time limit | `Failed with result 'timeout'.` (`UNIT_RESULT=timeout`) |
   | failed otherwise | `Failed with result 'R'.` for any other R, or a failed start job |
   | still running | started, no outcome yet |

4. **The window** is the last hour (`--window`), or two periods and the timer's
   `RandomizedDelaySec` + `AccuracySec` when that is longer, so an hourly or daily unit always
   has a run inside it.

## The four conditions judged from a unit

Each is judged on its own, opens and clears on its own, and is said once.

- **NO RUN COMPLETED**: at least one run ended in the window and none completed, whatever the
  failure was: killed, exit code, signal.
- **RUNS KILLED AT THE TIME LIMIT**: more than 25% of the runs that ended were killed at the
  limit (`--killed-share`), and at least 2 (`--min-killed`). This is the one that carries the
  incident: `envoy-role-holder-watch` completed a few runs in most of its bad hours (36 of 102 at
  18Z, 22 of 113 at 00Z), so "none completed" held only at 06Z and 10Z-12Z on 2026-11-02, while
  the killed share was over 25% in every hour from 2026-11-01 16Z to 2026-11-02 13Z (26% to 98%).
  It also speaks before the hour is gone: at a 30 s period, all-killed crosses 25% of the window
  after about 15 minutes. The bar comes from the journal on this box, 2026-11-01 09Z to
  2026-11-02 14Z: `omp-memory-watch` at 43% (36 of 83) and 28% (13 of 47) on its two worst hours
  and 0-17% on the others; `omp-login-watch` at 13% (7 of 52); `disk-fill-watch` at 23% (3 of
  13). One run in four killed is a unit whose limit no longer fits the box. It clears at half
  the bar (`--clear-factor`), so it never flaps on 25%.
- **TIMER STOPPED FIRING**: an enabled timer that is not active, or an active one that has not
  fired for two periods + its slack + 2 min while its service is not running. For
  `OnUnitInactiveSec` the clock starts when the service last went inactive. A disabled timer that
  is stopped is someone's choice and is not reported. A dead timer is the same silence as a unit
  that never completes, and leaves nothing in the journal to count.
- **RUN NEVER ENDS**: one run has lasted longer than its limit. The floor is two periods + slack
  + 2 min, and at least 15 min (`--stuck-after`). When twice the p99 of the unit's own completed
  runs of the last two days is longer, that is the limit. For a oneshot the limit is never past
  `TimeoutStartSec` + `TimeoutStopSec`, even below the floor: by then systemd itself should have
  ended the run. The p99 is at most the longest completed run, so the raise never passes twice
  the longest. A run that never ends holds the unit, so its timer cannot start the next one.

  Why the p99: replayed over this box's user journal (2026-11-01 10:50Z to 11-03 03:46Z, 41 h),
  the floor opened this 13 times, and every one of those runs ended by itself: transcript-import
  (every 10 min, floor 22 min) ran 32-45 min, omp-transcript-offload (15 min, 33 min) 36-40 min,
  a repository mirror (5 min, 15 min) 20-41 min, agent-files-archive (20 min, 43 min) 69 and 88 min.
  Twice the p95 left 12 of them: taken from the runs before each one, as the watch must, the
  p95 was 8-19 min. Twice the p99 leaves 5, each a run past twice the p99 of that unit's runs
  before it: the mirror's first long runs, agent-files-archive's first two, one offload run.
  Tried as well, replayed the same way: max(2 x p95, 1.25 x the longest) left 6; twice the
  longest left 4, but one long outlier would then hide a hang for two days. The history is read
  only for a unit already running past the earliest its limit can be, in one `journalctl` read
  of the last two days (45,000 records, 1-5 s here), and kept for an hour in the state file
  (`run_times`). Only a oneshot's `Finished` ends a run, so other service types keep the floor,
  and their `TimeoutStartSec` bounds only their start (infra977-trial-feed: `Type=simple`, 2 min,
  runs for hours).

  A unit may be given its own limit instead, with `max_run=` on its line in the expected list
  below. That replaces everything here, including `TimeoutStartSec` + `TimeoutStopSec`: it is
  the owner's own statement of how long a run may take. `agent-files-archive` is the first one
  (`max_run=3h`): it fires 20 min after each run ends and normally takes 5-7 min, but after
  the reset of 2026-11-04 one run took 47 min, because the shared request budget of its
  tracker client sleeps while every lane re-downloads at once, and this watch opened RUN NEVER
  ENDS at 43 min on a run that was working.

## The timers that must be there: devbox/expected-timers.txt

The four conditions above are judged from a unit, so a timer that is simply **gone** is
invisible to all of them. On 2026-11-04 the box stalled from 16:40Z and was reset at
18:05:53Z, and three watches did not come back: `omp-memory-watch`, `omp-root-hang-watch` and
`omp-login-watch`, each a linked `.service` in `~/.config/systemd/user` with no linked
`.timer`. `systemctl --user list-timers` showed nothing, which is exactly what a healthy box
with no such timer looks like. The memory watch was gone from 17:10:15Z to 18:59:11Z and
nothing said so.

So one plain list, `~/.dotfiles/devbox/expected-timers.txt` (`--expected`), holds the timers
this box must have: one name per line, a `#` comment saying who owns it, and optional
`key=value` fields after the name (`owner=`, `max_run=`). Three conditions come from it, each
said once and cleared on its own:

- **EXPECTED TIMER MISSING**: the user manager has no such timer (`LoadState=not-found`), or
  it is masked. Nothing starts its service and nothing else would ever say so.
- **EXPECTED TIMER NOT ENABLED**: the unit file is there but it is not enabled, so the next
  reset of the box loses it, even if it happens to be running now.
- **EXPECTED TIMER HAS NOT RUN**: it is enabled and has not fired for more than three of its
  own periods. Three, not the two of TIMER STOPPED FIRING, because this is the coarse net for
  a timer that came back wrong, not the fine one for a timer that stops.

The cost is one small file read and, only for a name this box does not have enabled, one extra
`systemctl show`; on a healthy box that call is never made.

`installers/expected-timers.sh` is the other half: it links and `enable --now`s every timer in
the same list, and is safe to run again at any time. A link is not an armed timer, which is
the whole of what went wrong on 2026-11-04. `installers/expected-timers.sh --check` says what
is unarmed and changes nothing.

## The streams' standing jobs

`scripts/stream-job` runs each stream's own standing job from a job file the stream owns
(`scripts/stream-job.md`). This watch reads the same state, `~/.local/state/stream-jobs/<name>/`,
and says **STREAM JOB NOT FINISHING** when a job has not finished within three times its own
`every`, naming the owner its job file gives, when it last started and finished, its last exit
and the last line it printed. It clears on the first run that finishes. The runner itself
alerts nobody: one voice for a quiet job, and it is this one.

## Units with their own failure handler

A unit whose `OnFailure=` names a handler (omp-session-sql-sweep, omp-login-watch,
transcript-mirror here) says its own failed runs, so NO RUN COMPLETED and RUNS KILLED are not
judged for it: each failure gets one message, from the handler. Its timer stopping and a run
that never ends fail nothing and start no handler, so those two are still said. The list comes
from each service's `OnFailure=` property, not from a list in this script; `--report` marks these
units `(failed runs told by <handler>)`.

What it gives up: a unit whose handler is broken, or says nothing, has its failed runs said by
nobody.

## What a notice says

All of one run's news is one message of at most 600 characters:

- `UNIT RUNS NOT COMPLETING on <host>, for the box-health lead:` and one item per new condition,
  `<service> <CONDITION> - <why>`; items that do not fit are counted (`N more in the report.`);
  conditions that cleared in the same run follow as `also <service> <CONDITION> cleared after
  <time>`;
- when the run found nothing about a unit's runs and only something about the expected list or
  a stream job, the first words are that condition instead, so the reader is not sent to the
  journal of a unit that is not there: `EXPECTED TIMER MISSING on <host>, for the box-health
  lead: <timer> EXPECTED TIMER MISSING - <timer> (owner <who>) is in <list> but this user
  manager has no such timer (not-found); nothing starts its service, and nothing will say so.
  Full report: <path>`;
- or, when the run only clears, one line: `UNIT HEALTH CLEARED on <host>: <name> <CONDITION>
  cleared after <time>; ...`;
- and last, always, `Full report: <path>`.

The report, `reports/<UTC YYYYmmddTHHMMSSZ>-open.txt` or `-clear.txt` beside the state file
(kept 7 days, pruned by each run), holds what the message used to: for each new condition the
journal counts for the window (started, completed, killed at the unit's `TimeoutStartSec`,
failed otherwise by result, still running), the timer and when it fires next, the last error
systemd logged for the last failed run, the last line the unit printed in that run, what to read
(the `journalctl` command, `systemctl --user status`, the unit's `Documentation=`) and the
advice; then the clears, and the `--report` table of every timer.

A condition that still holds is no new fact and is not said again; a new unit or a new condition
of a unit already open is. A unit whose timer is gone (removed, or a transient timer stopped) has
its conditions closed with that said.

Volume, replayed: every 5 min over the user journal (2026-11-01 10:50Z to 11-03 03:46Z, 41 h;
timers' run starts, ends and stops rebuilt from it, a stopped timer taken as disabled), the
previous version sends 81 messages, 48 a day, and this one 45, 26 a day: 13 → 5 RUN NEVER ENDS,
17 → 8 NO RUN COMPLETED and 7 → 6 RUNS KILLED (units with an `OnFailure=` handler:
omp-session-sql-sweep, transcript-mirror), no repeat after 2 h (7 before), and every message at
most 600 characters (the previous longest 5,020). In 2026-11-02 14:29Z to 11-03 01:50Z, while
the previous version ran on the box, the replay gives it 33 messages against the 34 the SRE
session received, and this one 20. The rest is mostly TIMER STOPPED FIRING (10 in the replay,
7 of them in one stall at 2026-11-01 17:25-17:40Z) and the clears. What it gives up: a run that
hangs in a long unit is said later (transcript-import at twice its p99, at most at 51.5 min, its
TimeoutStartSec + TimeoutStopSec, where the floor said 22 min), and a condition that lasts for
days is said once.

## Where notices go

The same route as every box watch here (`load-swap-watch.md`, "Where alerts go"):
`BOX_HEALTH_OWNER` when the service's environment sets it, else the box-health lead's role
(`notifications.role.lead-box-health`), else the SRE session
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`). Agent sessions only, never a person.

## What it will not catch

- **Its own silence.** Its timer is in the list it reads, but a watch that does not run cannot
  say so. Its 180 s limit is far above its pass (about 7 s at load 100), and it runs at `Nice=5`,
  above the lanes, for that reason. A pass that reads the two-day run time history takes ~5 s
  more.
- **A unit with no timer**: a long-running service, or work started by hand or by another unit.
- **A run that completes and does nothing useful.** It counts what systemd recorded, not what
  the unit was for; that is each unit's own business.
- **The journal's horizon.** A daily unit's two-day window is cut short where the user journal
  starts (about 30 hours back on sami-rocqperf on 2026-11-02).

## Turning it off

`systemctl --user disable --now unit-health-watch.timer`; nothing re-arms it until someone runs
`devbox/install.sh` again. Deleting the state file
(`/mnt/nvme/sami/tmp/unit-health-watch/state.json`, or `$XDG_STATE_HOME/unit-health-watch/`
without that disk) forgets every open condition, so each one still true is said again, and the
run time cache, which the next long run reads again. Reports sit in `reports/` beside it.
