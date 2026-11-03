# transcript-lag-watch

Says when the transcript store stops receiving this host's agent sessions: the store's newest
entry from this host is older than 45 minutes on two checks in a row. A session that is not in
the store is invisible to `tq`. The watch only reports. It starts, stops and changes nothing.

Armed by `devbox/install.sh` as the `transcript-lag-watch` user timer
(`devbox/transcript-lag-watch.{service,timer}`), every 10 minutes. On a machine with no
`transcript-import` unit and no `host` in `~/.config/transcript-store/config.toml`, each run logs
"nothing to watch" and ends.

```sh
transcript-lag-watch --dry-run                       # decide and print; send nothing, write nothing
transcript-lag-watch --dry-run --write-state --state-dir /tmp/tlw --lag-min 1   # twice: see an alert
```

## The gap it closes

Notify review G5 (2026-11-03): nothing alerted on store lag. `unit-health-watch` sees the
importer's unit, not the age of the data. At 02:10Z the store's newest entry from sami-rocqperf
was 132 min old (its importer was stopped at 00:16Z), and the ossv-agent-* hosts were ~31 h
behind.

## What one run does

1. **Ask the store** through `tq sql` (the read-only user): the newest `messages.ts` whose `host`
   is this host's name, and the host's last `loads.loaded_at`. The name is the importer's `host`
   in the store's `config.toml`, else this machine's name, else `--host`. Rows stamped more than
   5 min ahead of the store's clock are left out, so one file with a wrong clock cannot hide the
   lag. The `tq` call has a 45 s limit end to end (`--query-timeout`), and the server a limit of
   three quarters of that. Measured on sami-rocqperf at load ~350 on 2026-11-03: the query 0.5 s,
   the run 3.8 s.
2. **Read the importer**: `systemctl --user show transcript-import.service transcript-import.timer`.
3. **Judge**: the lag is now minus that newest entry. Over the bar on two checks in a row
   (`--lag-min 45`, `--checks 2`) is an alert.

## The bar

Measured by replaying a 10-minute check over this host's load history in the store (each
batch's `loads.loaded_at` and its newest `messages.ts`), 2026-10-26 02:50Z to 2026-11-03 02:30Z,
1,151 checks. The median lag was 8 min. The importer's runs in the 40 h of journal before
2026-11-03 03:00Z: 209 runs, median 2.3 min, 90th percentile 10 min, 6 over 30 min, longest
44.6 min.

| bar, 2 checks | episodes | messages | of which normal slow imports |
| --- | --- | --- | --- |
| 30 min | 6 | 20 | 2 (2026-10-31 15:20Z and 22:40Z, peak 49 min, gone after one check) |
| 45 min | 4 | 14 | 0 |

The four at 45 min are three outages of 4 to 7 hours (10-26 04:10Z, 10-27 09:40Z, 10-28 16:30Z,
peaks 270, 330 and 447 min) and the deliberate stop of 11-03 00:16Z (peak 123 min). A bar of 45
min speaks 10-20 min after the lag crosses it, so about 55-65 min after the last good import.

## What a message says, and when

Every message is at most 600 characters and ends with `Full report: <path>`, a file in
`<state dir>/reports/<UTC time>-<kind>.txt` with the readings, the last 12 checks, the importer's
units and its last 15 journal lines, the pause file's state, and the commands to run. Reports
older than 7 days are removed on each run.

| kind | when | text |
| --- | --- | --- |
| `lag` | lag over the bar on 2 checks in a row, once per episode | host, newest entry time, lag, the importer's timer state and last run result, the pause file |
| `lag-update` | only on a new fact: the lag doubled since the last message, or the importer is in a state (timer state, last run result) not yet said in this episode | the same, with the lag at the last message |
| `caught-up` | the first check back under the bar after an alert | one line |
| `store-unreachable` | `tq` failed or timed out on 2 checks in a row, once per episode | the error, the importer state |
| `store-back` | the first answer after that message | one line |

A run starting or ending is the importer working, so it is not a new fact. A clear comes only
after an alert. One failed query is not an episode.

Where a message would pass 600 characters, what it drops is the advice first, then the pause
file's path: the host, the lag, the importer's state and the report's path always stay. With the
longest host name DNS allows and this box's state directory, only the advice goes.

## Pausing it for a deliberate stop

A file `paused` in the state directory holds every alert and re-send. The state directory is
`/mnt/nvme/<user>/tmp/transcript-lag-watch` on a box with the big disk and
`~/.local/state/transcript-lag-watch` on a machine without one (`--state-dir`); every message
and report names the one this machine uses. The file's first word may be a UTC time, the pause's
end; the rest is the reason:

```sh
echo '2026-11-03T08:00Z importer stopped for a timed benchmark (INFRA-602)' > /mnt/nvme/sami/tmp/transcript-lag-watch/paused
rm /mnt/nvme/sami/tmp/transcript-lag-watch/paused      # end it
```

With no time first, the pause holds until the file is removed; every run logs that it is paused.
Past its end time the file is ignored, and the next alert says it expired. Checks still count
while paused, so a lag still over the bar when the pause ends is said on the next check. A clear
of an alert already sent still goes. Every alert names the pause file.

## Where messages go

The same route as every box watch here (`unit-health-watch.md`, "Where notices go"):
`BOX_HEALTH_OWNER` when the service's environment sets it, else the box-health lead's role
(`notifications.role.lead-box-health`), else the SRE session
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`). Agent sessions only, never a person.

## Expected volume, and what it gives up

- **Normally 0 messages a day**: no normal import came near 45 min for two checks in 8 days.
- **An outage**: an alert, one more each time the lag doubles (at ~2 h, ~4 h), one more for each
  new importer state, and one line when it catches up: 2-5 messages. The replay above gives 14
  messages in 8 days, all of them for real outages or the deliberate stop.
- **A deliberate stop with a pause file**: 0.
- It gives up an alert for a lag of 30-45 min, and anything shorter than two checks. Those were
  the two slow imports of 10-31, which cleared on their own.

## What it will not catch

- **A quiet host**: the lag is the age of the newest message, so a host where no session writes
  anything for 45 min looks like a stopped importer. sami-rocqperf always has sessions writing.
- **Missing rows in the middle**: a load that skips some files but brings newer ones in.
- **Other hosts**: each host's own watch says its own lag; this one asks only for its own name.
- **Its own silence**: `unit-health-watch` reads this watch's timer, as every other.

## Turning it off

`systemctl --user disable --now transcript-lag-watch.timer`; nothing re-arms it until someone
runs `devbox/install.sh` again. Deleting the state file (`state.json` in the state directory)
forgets the open episode, so a lag still over the bar is said again after two checks.
