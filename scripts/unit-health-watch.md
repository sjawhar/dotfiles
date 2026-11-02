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

## The four conditions

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
- **RUN NEVER ENDS**: the service has been running for longer than two periods + slack and
  longer than 15 min (`--stuck-after`). A run that never ends holds the unit, so its timer
  cannot start the next one.

## What a notice says

All of one run's new conditions go in one message, one block per unit:

- the conditions and why each holds;
- the journal counts for the window: started, completed, killed at the unit's `TimeoutStartSec`,
  failed otherwise by result, still running;
- the timer, its trigger, its state, when it last fired and when it fires next;
- the last error systemd logged for the last failed run (`start operation timed out.
  Terminating. / Main process exited, code=killed, status=15/TERM / Failed with result
  'timeout'.`), and the last line the unit printed in that run;
- what to read: the `journalctl` command for the window, `systemctl --user status`, and the
  unit's own `Documentation=`.

A condition still open after `--escalate` (2 h) is said again once, and every clear is said.
A unit whose timer is gone (removed, or a transient timer stopped) has its conditions closed with
that said.

## Where notices go

The same route as every box watch here (`load-swap-watch.md`, "Where alerts go"):
`BOX_HEALTH_OWNER` when the service's environment sets it, else the box-health lead's role
(`notifications.role.lead-box-health`), else the SRE session
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`). Agent sessions only, never a person.

## What it will not catch

- **Its own silence.** Its timer is in the list it reads, but a watch that does not run cannot
  say so. Its 180 s limit is far above its pass (about 7 s at load 100), and it runs at `Nice=5`,
  above the lanes, for that reason.
- **A unit with no timer**: a long-running service, or work started by hand or by another unit.
- **A run that completes and does nothing useful.** It counts what systemd recorded, not what
  the unit was for; that is each unit's own business.
- **The journal's horizon.** A daily unit's two-day window is cut short where the user journal
  starts (about 30 hours back on sami-rocqperf on 2026-11-02).

## Turning it off

`systemctl --user disable --now unit-health-watch.timer`; nothing re-arms it until someone runs
`devbox/install.sh` again. Deleting the state file
(`/mnt/nvme/sami/tmp/unit-health-watch/state.json`, or `$XDG_STATE_HOME/unit-health-watch/`
without that disk) forgets every open condition, so each one still true is said again.
