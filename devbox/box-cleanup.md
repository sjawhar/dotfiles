# box-cleanup: seven cleaners, one schedule, one log, two alerts

Until 2026-11-04 this box ran seven disk cleaners on seven timers. Sami folded its 23 user timers
into about four (dispatch://INFRA-993/ask/d44b73ba-3ac6-4307-9dd7-065cb035ec9b, 21:02Z); these
seven became one.

**This is one schedule and one log, not a new deletion policy.** Each cleaner runs as it stands,
with its own caps, ages, refusals and environment. What is new: when they run, that the run is
recorded, and that something speaks when the cleaning stops working.

## What it runs, in order

| step | what it was | kept rules |
| --- | --- | --- |
| `tmp-empty-prune` | daily | empty `tmp*` entries older than two days in the big disk's TMPDIR |
| `tmp-stale-reap` | hourly | stale top-level entries of the root-resident `/tmp`, with its own `TMP_REAP_RUN_SECONDS=1680` budget |
| `session-scratch-reap` | **every 2 minutes** | per-session scratch directories whose session is gone |
| `session-tmp-reap` | daily | report only: what the big disk's TMPDIR would lose |
| `gocache-cap` | hourly | Go build cache trimmed past 25 GB, wiped past 50 GB |
| `nvme-cache-cap` | hourly | uv, cargo and Go module caches under their caps |
| `docker-cap` | hourly | build cache under 50 GB; unusable data removed past 80% of Docker's disk |

Cheapest first, so a long `docker-cap` cannot keep the small sweeps from happening at all. Each
step keeps the `ConditionPath*` its own unit had: a step whose target is not on this machine is
recorded `skipped`, not run and not failed. A step that fails or is killed at its 30-minute
timeout is counted and the rest still run.

## What one run records

A line per step and a summary line in the journal
(`journalctl --user -u box-cleanup.service`), and one JSON object per run appended to
`~/.local/state/box-cleanup/log.jsonl` with each step's bytes, seconds and exit, and every
watched filesystem's free, total and floor.

The bytes are **what that filesystem gained across that step**, not what the cleaner deleted:
eighty sessions write to these disks throughout, so a step can read negative, and the log prints
it as it is. The filesystems are found from the cleaners' own paths and keyed by device — on
sami-devbox `/home`, `/mnt/nvme` and `/var/lib/docker` are one disk, and 216 agentbox loop
mounts are not the cleaners' business.

## When it speaks, and when it does not

Two alerts, over Envoy, to `notifications.role.lead-box-health`, falling back to the SRE's
session when nobody holds the role — the route `unit-health-watch`, `load-swap-watch` and
`disk-fill-watch` use. Never to a person's email, never to Slack.

1. **A filesystem is under its floor after the run.** The floors are `disk-fill-watch`'s own
   (`scripts/disk-fill-watch`, `FLOORS`): 18 GiB on `/`, 84 GB on `/mnt/nvme`, 20 GiB elsewhere,
   each held to a tenth of its own filesystem so a small partition is not floored at a size it
   never has. The point of the alert is that the box cannot clean its way out: what is left is
   live work.
2. **It freed nothing on three days running.** One quiet day is a box whose caches are already
   under their caps and whose sessions all still live. Three is a cleaner that has stopped
   working, which is otherwise invisible — the failure the seven separate timers had.

It does not alert on a failed step: `unit-health-watch` already reports a unit whose runs fail or
never end, and it reads `max_run=4h` for this one from `devbox/expected-timers.txt`.

## Running it by hand

```bash
box-cleanup --dry-run            # the steps, the filesystems, their floors; runs nothing
box-cleanup                      # the daily pass
box-cleanup --only docker-cap    # one cleaner, repeatable, still logged
box-cleanup --no-alert           # run and log, send nothing
box-cleanup --report             # the state and the last five runs
```

The seven scripts are untouched and still runnable on their own
(`devbox/docker-cap`, `devbox/gocache-cap`, …), which is what to reach for when a disk needs
clearing now rather than at midnight.

## Overrides, for a test against a scratch tree

`BOX_CLEANUP_STATE`, `BOX_CLEANUP_LOG`, `BOX_CLEANUP_ENVOY_URL`, `BOX_CLEANUP_STEP_TIMEOUT`, and
`BOX_CLEANUP_DF`, a command printing `<mount> <avail> <total>` lines, so a test can hand it
filesystems that are not this machine's.

## What the daily schedule costs

Four of the seven ran more often than daily. Dead sessions' scratch directories and the caches
now grow for up to a day between sweeps. What carries that: the fleet's Datadog disk monitors
(`agent-infra/monitoring/disk.go`, which covers this box from 2026-11-04), this job's own floor
alert, and the seven scripts remaining runnable by hand. One thing it improves: a scratch
directory reaped two minutes after its session died once refused an `agentbox move` resume
(`scripts/tests/test_agentbox_move.py`, the 10:42Z spec); a day's grace removes that race.
