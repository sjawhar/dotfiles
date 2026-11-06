# box-cleanup: seven cleaners, one schedule, one log, two alerts

The seven disk cleaners share one timer and one log
(dispatch://INFRA-993/ask/d44b73ba-3ac6-4307-9dd7-065cb035ec9b). The timer fires every
15 minutes, with up to a minute of random delay. Costly sweeps remain daily; cheap cache caps
check their own usage thresholds on every pass.

**This is one schedule and one log, not a new deletion policy.** Each cleaner runs as it stands,
with its own caps, ages, refusals and environment. What is new: when they run, that the run is
recorded, and that something speaks when the cleaning stops working.

## What it runs, in order

| step | minimum interval | reason and kept rules |
| --- | --- | --- |
| `tmp-empty-prune` | 86400 s (daily) | walks temporary entries; empty `tmp*` entries older than two days in the big disk's TMPDIR |
| `tmp-stale-reap` | 86400 s (daily) | walks the root-resident `/tmp`, with its own `TMP_REAP_RUN_SECONDS=1680` budget |
| `session-scratch-reap` | 86400 s (daily) | session-directory census and liveness checks; only scratch directories whose session is gone |
| `session-tmp-reap` | 86400 s (daily) | costly TMPDIR census (409-828 s measured); still report only |
| `gocache-cap` | 0 s (every pass) | cheap size check; Go build cache trimmed past 25 GB, wiped past 50 GB |
| `nvme-cache-cap` | 0 s (every pass) | cheap usage checks; uv, cargo and Go module caches under their own caps |
| `docker-cap` | 0 s (every pass) | cheap filesystem check; acts only above `DOCKER_CAP_HIGH_PCT` (default 80%), keeping its own caps, ages and pull-in-flight refusal |

The existing step order, commands and environments are unchanged. Each step keeps the
`ConditionPath*` its own unit had. Missing targets and minimum intervals that have not elapsed
are recorded as `skipped`, with a reason and a null exit. Neither changes its failure streak,
last exit or last successful run. A failure or 30-minute timeout is counted and the rest run.

`step_last_success` in `~/.local/state/box-cleanup/state.json` records each successful
completion in epoch seconds. A daily step is due on the first pass at least 86400 seconds
after that completion. Missing success state makes it due, including the first pass after
installing interval tracking. A failed attempt does not postpone a retry. `--only` selects
steps but does not bypass their intervals.

## What one run records

A line per step and a summary line in the journal
(`journalctl --user -u box-cleanup.service`), and one JSON object per run appended to
`~/.local/state/box-cleanup/log.jsonl` with each step's `reclaimed` and `moved` bytes, seconds
and exit, or its skip reason, and every watched filesystem's free, total and floor.

**`reclaimed` is what the cleaner itself says it removed**, read from a `reclaimed-bytes: N`
line in its own output. A cleaner that prints no such line is reported as not having said, and
its step contributes nothing to the run's total: the total names the steps that did not report
rather than filling them in.

**`moved` is what that filesystem did while the step ran**, which is a different thing and is
labelled as one. Eighty sessions write to these disks throughout, so it can read negative, and
the log prints it as it is. **It is never attributed to the step.** On 2026-11-06 it was: the
run credited `session-tmp-reap`, which is report-only and deletes nothing, with freeing 192.9
GB, and charged `docker-cap`, which had just removed 28 images, with −40.5 GB. A df delta on a
shared mount bills every lane's writes to whoever reads the clock twice.

A cleaner that reports must report **zero when it did not delete**. In report-only mode its
candidate total is what *would* go, and a caller counting that publishes a deletion that never
happened.

The filesystems are found from the cleaners' own paths and keyed by device — on sami-devbox
`/home`, `/mnt/nvme` and `/var/lib/docker` are one disk, and 216 agentbox loop mounts are not
the cleaners' business.

## When it speaks, and when it does not

Two alerts, over Envoy, to `notifications.role.lead-box-health`, falling back to the SRE's
session when nobody holds the role — the route `unit-health-watch`, `load-swap-watch` and
`disk-fill-watch` use. Never to a person's email, never to Slack.

1. **A filesystem is under its floor after the run.** The floors are `disk-fill-watch`'s own
   (`scripts/disk-fill-watch`, `FLOORS`): 18 GiB on `/`, 84 GB on `/mnt/nvme`, 20 GiB elsewhere,
   each held to a tenth of its own filesystem so a small partition is not floored at a size it
   never has. This check still runs when some or all cleaners are skipped; the log says which
   ones were not due or had no target on this machine.
2. **Cleaning has stopped working.** Per-step failure streaks count attempted runs only, clear
   on success, and speak at three failures. Otherwise the freed-nothing rule speaks at three
   eligible daily observations with no positive per-step space gains. Only attempted work
   contributes: a pass with every selected step skipped does not increment the count or send
   that notice. `zero_checked_at` in state keeps observations at least 86400 seconds apart,
   including mixed passes and repeated `--only` passes. A positive gain on any pass clears
   the zero-gain streak, even if other steps were skipped or the net change was negative.

Both streak notices repeat only when their count doubles. `unit-health-watch` still reports
a unit whose runs fail or never end, using the unchanged `max_run=4h` in
`devbox/expected-timers.txt`.

## Running it by hand

```bash
box-cleanup --dry-run            # due/skipped steps and reasons, filesystems and floors; runs nothing
box-cleanup                      # due work on this pass
box-cleanup --only docker-cap    # select one cleaner, repeatable, still logged
box-cleanup --no-alert           # run and log, send nothing
box-cleanup --report             # the state and the last five runs
```

The seven scripts are untouched and still runnable on their own
(`devbox/docker-cap`, `devbox/gocache-cap`, …), which is what to reach for when a disk needs
clearing now rather than waiting for its next due pass.

## Overrides, for a test against a scratch tree

`BOX_CLEANUP_STATE`, `BOX_CLEANUP_LOG`, `BOX_CLEANUP_ENVOY_URL`, `BOX_CLEANUP_STEP_TIMEOUT`, and
`BOX_CLEANUP_DF`, a command printing `<mount> <avail> <total>` lines, so a test can hand it
filesystems that are not this machine's.

## Why one frequent schedule

A disk that fills by hundreds of GB per hour cannot wait for midnight: the observed image
growth left only 1 h 37 min before its floor. Fifteen-minute cap checks fit several passes
inside that window without adding a timer. They do not override a cleaner's decision to do
nothing. The session and temporary-file walks still run no more often than daily; in
particular, the 409-828-second TMPDIR census is not repeated every quarter hour.

The service remains a single sequential run with its existing 4-hour timeout. systemd does
not start another copy while it is active, so a slow daily pass can delay the next cap check.
