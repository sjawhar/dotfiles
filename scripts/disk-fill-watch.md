# disk-fill-watch

Every 5 minutes, records free space on each watched filesystem of **this** machine and computes
the fill rate from those `df`/`statvfs` samples. It alerts over Envoy when the disk is about to
land — either because free space is under a hard floor, or because **three consecutive scheduled
five-minute samples** each put time to full at the df free-space fill rate under the horizon.
It also samples the **IOPS** of each mount whose device has a provisioned cap, and alerts when
that device stays at it.

## Why it exists

On 2026-10-27T00:00:31Z the root filesystem of sami-rocqperf reached zero bytes free and stayed
there for 11 seconds. From the previous boot's journal:

```
00:00:31Z rsyslogd[1617]: file '/var/log/syslog'[7] write error ... No space left on device
          (10,788 of these between 00:00:31Z and 00:00:42Z, of 10,796 ENOSPC lines in all)
00:00:32Z dockerd[2600810]: Error running exec ...: OCI runtime exec failed:
          write /tmp/runc-process3426999125: no space left on device
00:00:33Z systemd-journald[1593242]: Failed to open system journal: No space left on device
00:00:33Z bet-screen-slot[1679672]: screen-pass.sh: line 112: echo: write error: No space left on device
```

Nothing alerted. The only instrument running was a watch loop inside an agent session
(`/mnt/nvme/sami/tmp/infra-wake/disk-floor-watch.sh`), which sampled `df` every ten minutes
against a fixed floor — and which died with its session; its log stops at 08:58:33Z.
(dispatch://INFRA-613)

**A floor alone is not enough.** That night / fell from 62 GiB free at 18:12:24Z to 1 GiB at
23:53:08Z — 61 GiB in 5 h 41 min — and over its last 38 minutes it fell at 31 GiB/h (20.4 GiB
free at 23:15:10Z, 1 GiB at 23:53:08Z). The floor watch's own log shows it crossing an 18 GiB
floor at 22:09:56Z — 110 minutes before the fill — so the floor said "low" all night and never
said "about to land". The number that says that is the fill **rate**, and the horizon it buys.

## What it decides

Per mount, per run:

| | |
|---|---|
| Sample | `statvfs(mount).f_bavail × f_frsize` — the same free-space value `df` reports to an unprivileged writer, which is where ENOSPC arrives. Appended every run, whether or not anything is wrong: a silent log cannot be told from a dead watch |
| **df free-space fill rate** | bytes/s of **fall** between the oldest sample inside `--rate-window` (1 h) and now; this is the only rate used for time to full |
| Time to full | free ÷ df free-space fill rate, when falling |
| Rate candidate | time to full < `--horizon` (2 h), with at least `--min-span` (25 min) of df samples behind the rate and free at a new low for the hour |
| **Crossing** | free < the mount's hard floor, **or** three consecutive rate candidates at least `--confirmation-spacing` (5 min) apart |
| Last-two-sample rate | the same two-endpoint fall, over the last two samples only. It is read for one decision and nothing else: whether a crossing may skip the grace window |
| Urgent | free ÷ the **faster** of those two rates is under `--urgent-horizon` (30 min) |
| Alert | after `--grace` (240 s, so a second consecutive run confirms it) — **or at once, on the sample that opens the crossing, when it is urgent** |
| Said again | only on a **new fact**: free fell **25%** below the level the last message reported, free fell **under the floor** (a crossing opened on the rate alone), or time to full fell **under 1 h**. Never on a clock |
| Clear | free ≥ floor × 1.5 **and** no df free-space fall that reaches full inside twice the horizon |

### A crossing is said again only on a new fact

In the 48 h to 2026-11-03 this watch sent 150 messages, 454 k characters — 66% of every
watcher message the SRE received. **52 of them were `DISK LOW (STILL …)` re-sends of 4.2-7.2 KB
each**, sent by a 30-minute clock while one crossing hovered, and the SRE acted on none of
them; 20 covered a single `/mnt/nvme` crossing between 16:34Z and 00:06Z. A new free-space
number is not a new fact (dispatch://INFRA-672).

So the clock is gone and the 5 GiB `FLOOR_STEP` with it. An open crossing is said again when,
and only when, this run holds one of three facts, and the message says which:

| Fact | Said as | Why this one |
|---|---|---|
| free fell ≥ `RESEND_FALL_FRACTION` (25%) below the level the last message reported | `Said again: free fell 42% below the 15.9 GiB last reported.` | a proportion, not a fixed step: 5 GiB off a 3.7 TB array is noise, and 5 GiB off a root disk with 9 GiB left is the disk |
| free fell under the mount's floor | `Said again: free fell under its 78.2 GiB floor.` | a crossing opened on the fill rate alone reaching its floor is new |
| time to full under `RESEND_HORIZON` (1 h) | `Said again: under 1 h to full (8 min 36 s).` | said once while it holds, and again if the disk climbs back out and falls in |

Replayed against the night of 2026-11-02, the crossing that opened at 00:51:43Z and ran to
03:23Z says its first alert and then speaks on the falls that mattered, including **03:13:10Z,
`/mnt/nvme` at 9.2 GiB free, falling 68.9 GB/h, 8 minutes from full** — a 42% fall below the
15.9 GiB of its previous message. `scripts/tests/test_disk_fill_watch_messages.py` holds that
case with the journal's own samples.

**The headline never contradicts the body.** `STILL DESCENDING` is used only while the disk is
measurably falling; under the floor and flat or rising it is `STILL UNDER THE FLOOR`, and above
the floor with no fall it is `STILL OPEN`. Eight of the 48 h re-sends said "STILL DESCENDING"
over a body that read `rising 20.2 GB/h` or `free space is flat` (2026-11-01T19:54Z, 19:59Z,
20:09Z, 08:51Z, 09:21Z).

### Every message is at most 600 characters, and names its report

A crossing message carries the decision — mount, host, free, rate, time to full, why it is
said, and the top three writers on that filesystem with their owning sessions — and ends with
`Full report: <path>`. The report holds what used to be in the message: the writers in both
groups with their device evidence, the guard's kept-growth rows, the young `/tmp` entries, the
farm kinds already refused, and the DO paragraph.

| | |
|---|---|
| Where | `<state dir>/reports/<UTC YYYYmmddTHHMMSSZ>-<kind>.txt`, i.e. `/mnt/nvme/sami/tmp/disk-fill-watch/reports/` on this box |
| Kinds | `crossing-<mount>`, `clear-<mount>`, `withdrawn-<mount>`, `iops-<mount>`, `iops-clear-<mount>`, `entry-<root>`, `entry-digest` |
| Pruned | every run deletes reports older than 7 days |
| Read it | `scripts/disk-fill-watch --report` prints the newest one; any message's own path is in its last line |

A clear, a withdrawal and an IOPS clear are **one line** each: nothing is being asked of the
reader.

**A sawtooth is not a leak.** `/mnt/nvme` on this box swings about 100 GB up and down every hour
as grade jobs land and are cleaned (its 10-minute log on 2026-10-26 reads 295 GB → 479 → 378 →
340 → 232 free), so an hour of negative slope on its own fires on every downstroke. The new-low
test is what keeps it quiet: only a fall to a level the last hour has not seen is a fall towards
zero.

A one-minute fall is recorded, not sent. Three scheduled five-minute confirmations span at least
ten minutes, so a one-off writer burst that has already recovered cannot claim that the disk is
landing. The hard floor remains independent of this confirmation: below it the watch alerts
regardless of rate.

**An urgent crossing does not pay for the grace window.** The window costs a whole sampling
period. On 2026-11-01 `/mnt/nvme` crossed its floor at 00:25:51Z with 59.5 GiB free, alerted at
00:31:11Z and was at 7.9 GiB by 00:35:51Z: half of the ten minutes the box had went to the
window. Nothing an `rm` does inside one period makes a 30-minute deadline false, so a crossing
already that close to full alerts on the sample that opens it. The projection uses the faster of
the two rates because an accelerating fall hides inside an hour-long average: at 00:25:51Z the
hour read 91 GB/h (42 min to full, outside the mark) while the last two samples read 248 GB/h
(15 min, inside it). A crossing further out than the mark keeps the grace window unchanged, and
both alerts say which of the two rules sent them. The repeat rule and the clear are untouched.

### The agent path to a floor, and a bounded run

**This watch is the agent path to a disk floor on these boxes; Datadog posts the same floor
to #fleet-alerts for people** (Disk-writer floor alert, monitor 29275908, whose `/` threshold
is being raised to 19.33 GB — this watch's 18.0 GiB — in agent-infra). Datadog never wakes an
agent, and the floors here stay as they are. `guard-disk-writers` keeps its own floor check
for the writers it sheds and writes only to the journal.

Joined ±10 min against this watch's journal, 54 of Datadog's 86 floor alerts in the 48 h to
2026-11-03T01:50Z have a send from this watch; of the 32 that do not, 16 are before this
journal's retention (it starts 2026-11-01T09:50:56Z), 8 are Datadog renotifying a crossing
this watch had already said at its start, 6 are minutes when this watch's own sample read
**above** both floors (a trough shorter than its five-minute period, which Datadog's 15-second
metric sees and this does not), 1 is a run killed at the unit's time limit, and 1 is a dip
that opened and closed inside the grace window (2026-11-02T04:13:57Z, `/mnt/nvme` at exactly
78.2 GiB, back to 91.0 GiB by 04:19:05Z). None is a defect in the rules; the killed run is
what the budget below fixes.

Because this is the only path an agent hears, a fault in it is silence, so:

- **the growth watch's self-test faults are forwarded here** (below), and
- **a run bounds its own work** (`--run-budget`, 90 s, under the unit's `TimeoutStartSec=120`).
  Seven runs in the 6 h to 2026-11-03T02:39Z were killed at that limit (22:11:34Z, 22:16:34Z,
  23:01:37Z, 00:36:45Z, 00:41:45Z, 00:46:46Z, 00:51:46Z). Each had logged its mounts and its
  IOPS figure in the first two seconds and then spent the rest in the scratch scan, which at
  load 250 costs 5.4 s for `/tmp`'s 13,439 entries and 5.2 s for `~/src` and had no bound of
  its own once a directory walk started. Now the scan is bounded, each root keeps a cursor
  (`scan_cursor`) so the next run resumes where the last stopped and no entry is starved, the
  guard journal read is capped at 15 s, the `sudo` probe and the privileged sample are capped
  by what is left, and a run with too little left sends its alert **without** the writer
  sample, saying so, rather than being killed with its decision unsent.

  A deadline a step checks itself was still not enough: between 08:01Z and 08:36Z on
  2026-11-03, at load ~500 with IO pressure at 85%, four more runs were killed at 120 s and the
  completed ones took 84-102 s. Every run there spent its first 15 s on the guard journal read
  (`timed out after 15 s; retrying once with 45 s`), up to 45 s more on its retry, and then
  the scratch scan, whose directory listing and stats do not come back to look at a clock
  while the box stalls them. So now:

  | Step | Runs | Hard limit |
  |---|---|---|
  | statvfs of each mount | first, in a forked child | 10 s (`STATVFS_TIMEOUT`); the mount is skipped and logged |
  | the decision and the crossing send | in the run itself, before anything expensive | — |
  | writer attribution | forked child, before the send | 60 s, or the budget left less 20 s (`FINISH_RESERVE`) |
  | young-entry sizing and owner lookups | forked child, one per root | 15 s, or the budget left less 20 s |
  | guard journal read | LAST, a `sudo journalctl` subprocess | 15 s, a 45 s retry, both inside the budget left less 20 s |

  A step that does not answer in time is sent SIGKILL and not waited for: a child stuck in a
  read the kernel will not interrupt is removed with the unit's cgroup once the run exits.
  Each skip is one log line naming the step and why, and a crossing alert goes out even when
  every expensive step is skipped. The 20 s reserve covers the Envoy sends (8 s each), the
  state commits and the exit, inside the unit's 120 s.

### The floors

| Mount | Floor | Why |
|---|---|---|
| `/` | 18 GiB | Root holds every session's transcript, the journal, apt/dpkg state and `/tmp`, so a full root stops the whole box, not one lane. Root's band on 2026-10-26 afternoon was 25–28 GiB, so 25 would have paged all day; 18 leaves room to act |
| `/mnt/nvme` | 84 GB (decimal) | The grade farm's build-cache floor until 2026-10-31, when the farm raised its own build-cache floor to 100 GB; the alert floor stays at 84 GB (the script's `FLOORS` comment), and an alert between 100 and 84 GB is not sent, but every alert names the farm kinds already waiting (below), `build-cache` among them under 100 GB |
| anything else | `--default-floor`, 20 GiB | |

An alert on `/mnt/nvme` also names every grade-farm transfer kind whose own floor the current
free space is under (the farm's own floor table as of 2026-10-31: `generic-unmeasured` 150 GB,
`build-cache` 100, `registration` 68, `nightly-grade` 60, `frozen-drop` 44, `restore` 44,
`prepare-staging` 28, `grade-record` 24, `build-stage` 22.4, `suite-result` 21.6): that list is the
work the farm is already refusing.

**A floor is held to the device it names.** `--max-floor-fraction` (10%) caps every floor at a
tenth of its own filesystem, and the substitution is logged once, never alerted. On
sami-leanperf, `/mnt/nvme` is not an array at all — it is the same 247 GB `/dev/root` as `/` —
so the farm's 84 GB floor was 31.7% of it and the watch alerted at 82% used, flat, on its first
run, and would have alerted forever. Measured 2026-10-30, the two floors on sami-rocqperf are
3.6% (`/`, 531 GB) and 2.3% (`/mnt/nvme`, 3.72 TB) of their devices, so neither is held and
nothing there changed. The farm's per-kind list above is printed only where the floor was not
held: where it was, the farm's array is not on that machine and the list describes no work.

**Two mounts on one filesystem are watched once**, under the first name given, so a box where
`/mnt/nvme` is a directory on `/` no longer samples one disk twice and reports it twice.

### Two headlines, three endings

| Headline | When | Urgency |
|---|---|---|
| `DISK LOW` | free under the floor | No deadline **only when free space is flat or rising**; a disk under its floor and falling keeps the time to full |
| `DISK FILLING` | three confirmations of a fall that reaches zero inside the horizon | A deadline, with the time to full and the writers |

A disk that is not moving is never given a time to full. The first sami-leanperf alert said
"DISK FILLING … falling 0.0 GB/h over the last 5 min 20 s. At that rate it is 3558 h 42 min from
full", which is a standing condition dressed as a trend; the same headline for that and for
sami-rocqperf's real one that night (41.7 GB/h, 41 minutes to full) teaches the reader to skip
the headline.

**The closing paragraph is decided by the same time to full the first line prints**, never by
the floor alone. It used to key on the floor by itself, and on 2026-10-31T02:33Z one message
said "2 h 16 min from full" at the top and "there is no deadline here: the disk is under its
floor and is not filling" at the bottom. The ending a tired reader keeps was the wrong one.
The three states read differently:

| State | Ending |
|---|---|
| Under the floor, flat or rising | "There is no deadline here … a standing low rather than a fall" |
| Under the floor **and** falling | "This is both, and the clock is the half to act on … under the floor AND still falling, *N* from full" |
| Above the floor and falling | "DO, and there is a deadline, *N* from full" |

**What the rate rests on is said in the sentence that prints it.** The fill rate is the
difference between two samples — the oldest inside the window and now — however many lie
between, so every alert names the count: "falling 104.8 GB/h over the last 50 min, from the
first and last of 3 samples". Two samples are called out as "a first reading, not yet a trend".
Where free space both **rose and fell** inside the window, the alert reports the swing and its
high and low instead of handing over a deadline: on 2026-10-31 `/mnt/nvme` read 60 GiB free at
02:02Z, 102 at 02:11:50Z, 85.5 at 02:17:37Z, 95 at 02:27Z and 68.8 at 02:31:49Z, and three
people took three different pairs off that one curve and got three different, false, deadlines.
A window is varying when one rise inside it is at least as large as the move between its two
ends. This changes no decision — the crossing tests are untouched — only what the message
claims.

`--floor /=400` overrides a floor, for a deliberate test against a scratch threshold or for a
machine whose disks are not these.

## Who is writing, and whose session it is

A size walk sees what is already written. It cannot name what is being written **now**, which is
what an alert needs, so each alert carries a **single write-traffic interval**: `write_bytes` from
`/proc/<pid>/io` at two endpoints, `--writer-sample` (15 s) apart, sorted by the difference. For
each of the top writers the alert gives the bytes and the hourly equivalent **over that one
15-second sample**, the pid and its age, the **owning agent session**, the working directory
whatever disk it is on, and which filesystem the writer actually touches.

**The list is in two groups.** First the writers with the alerting filesystem under them, each
with one example path there (the largest file it has open for writing, else the cwd, else an
open directory); then, under a heading of its own, every writer whose traffic is elsewhere,
each saying `no cwd or open file on <mount>`. The ranking by write traffic is kept inside each
group, and the DO paragraph names only the first group.

**The test is the device id, never a path.** For each writer, `st_dev` of `/proc/<pid>/cwd` and
of every open regular file and directory, stat'd **through** `/proc/<pid>/fd/N`, is compared with
`st_dev` of the mount; so a symlink, a bind mount and a deleted-but-open file all resolve to the
device the kernel has open. On 2026-11-01T05:18Z the `/` alert listed pytest workers whose argv
named a `/home` path that is a symlink onto `/mnt/nvme`, and the SRE paused them; the real `/`
consumer was a `git worktree add` into `~/src`, and it kept running.

**A file held read-only there is not evidence.** The access mode comes from
`/proc/<pid>/fdinfo/<N>`, and only a writable file, an open directory (how git, pytest and dune
write, with `openat`) or the cwd puts a writer in the first group. Without that rule the same
pytest workers come first again on the strength of the interpreter they read: measured here at
2026-11-01T05:34Z, a worker with every output file on `/mnt/nvme` held
`/home/sami/.local/share/uv/python/…/python3.14` open on `/`. Such a writer is listed under the
elsewhere heading as `nothing open for writing on <mount>`, with what it does hold there.

A writer whose `/proc/<pid>/fd` cannot be read says that instead of being called elsewhere, and
so does one that exited during the sample or holds more than `FD_SCAN_LIMIT` (256) open files.

- The session is read from the process, never guessed, strongest source first: the lane
  worker's own `.ossv-workers/<name>/session` (ignored while `session_unconfirmed` sits beside
  it), else a session id on the nearest ancestor `omp`'s command line, else the transcript it
  holds open (`~/.omp/agent/sessions/**/<stamp>_<session id>.jsonl`), and `OMP_SESSION_ID`
  **last**. That variable is inherited: a session that starts a lane box's supervisor over ssh
  stamps its own id on every process below it for the life of the box, which is how
  load-swap-watch named 39 `python3` processes on `ossv-agent-repo` as a session running on
  sami-rocqperf at 2026-10-30T20:17:23Z. The id is then checked against `GET /v1/sessions` on
  Envoy, filtered to this box's host: one that is not live here says so and names the live
  owner when the worker's file or a box with exactly one live session can give one. An Envoy
  that does not answer leaves it unverified and named as before. When nothing names a session,
  the systemd scope is given instead and the alert says there is no session id.
- Each writer also carries its **tmux pane**, read once per alert from `tmux list-panes`;
  neither the pane nor the working directory can be inherited from another machine's session.
- `/proc/<pid>/io` counts writes to **every** filesystem, not only the one that is filling; it
  also counts rewrites and deleted output. Its displayed number is explicitly labelled
  **write traffic** and a single 15-second sample — not disk growth and never the number used for
  time to full. The group a writer is in says which filesystem its bytes land on; the number
  beside it does not, and both hold only at the sample instant.
- Another user's `/proc/<pid>/io` is refused to an unprivileged reader, and root owns the writers
  that failed first on 2026-10-27 (rsyslogd on `/var/log/syslog`, dockerd's `/tmp` exec scratch).
  So where `sudo -n` is allowed the whole sample — both `/proc` passes and the cwd and fd reads —
  runs as root in a child of this script (`--writers-json`, which reads `/proc` and prints JSON;
  it sends nothing and writes nothing). Where it is not, the unprivileged sample is used and the
  alert says how many processes refused the read, so root's writers are never reported as absent.
  `--sudo never` turns the privileged half off.
- **The child's stdout is the JSON document and nothing else**; every diagnostic it prints goes
  to stderr, which the parent logs with a `[privileged]` prefix. On 2026-10-31 all ten
  privileged samples of the day were lost to `JSONDecodeError: Extra data: line 1 column 5
  (char 4)` — a timestamped log line on that stdout, whose four-digit year parsed as the whole
  document. The `sudo -n true` probe in front of it gets **30 s**, not 5: the 06:04Z sample that
  day was lost to that deadline on a box at load 200+, where a call costing ~1.5 s idle has been
  measured at 10.1 s.

Above the `/proc` traffic list, an alert carries the **guard's kept-growth rows** for the
crossing mount from the last 60 minutes — `disk-writers: growth session:<id> on <mount>: kept X
GB of Y GB written`, read with `sudo -n journalctl -u guard-disk-writers.service --since -60min
-o cat`, newest last, at most six. That is the one thing a 15-second traffic sample cannot say:
which session's writes actually **stayed** on the disk over an hour. The read is best-effort — a
journal this run cannot read costs the alert only those lines, which are left out and the reason
logged.

The expensive reads happen only on a run that actually sends. An ordinary run is `statvfs` plus a
bounded `/tmp` scan: **0.3–0.4 s** measured on this box at load 43.

## The IOPS cap

A disk can stop a box without filling. `/mnt/durable` on sami-rocqperf is an Azure **P50**
premium SSD data disk, and its tier allows **7,500 completed operations a second** whatever the
hardware under it can do. Held there, every further read and write waits in a queue: free
space, load and memory all read normal while the box behaves as though it has stopped. That is
how the 2026-10-31T21:58Z outage of sami-rocqperf looked (dispatch://INFRA-586) — the device sat
at its cap for hours — and nothing on the box sampled the device, so afterwards "minutes at the
cap" was not a number anyone could produce.

Every run, for each capped mount:

| | |
|---|---|
| Device | `st_dev` of the mount, split into the major and minor numbers `/proc/diskstats` keys its lines by — the same lookup that places a writer on a filesystem. An **md** array or a **dm** mapping is read from its own line, never summed from its members, because the cap is enforced on the device the filesystem is mounted from |
| Counters | reads completed (field 4) + writes completed (field 8), stored with the instant they were read |
| IOPS | the difference in those counters divided by the seconds since the previous run. The first run after a restart or a deleted state file records and says the figure comes next run; a counter that went **backwards** (a reboot, or a different device under the mount) produces no figure either |
| Log | one line per run, whether or not anything is wrong: `/mnt/durable: 412 IOPS over the last 300 s, cap 7500 (5% of it, on nvme0n2)` |
| Alert | **three consecutive runs** at or over **90%** of the cap, said **once** per crossing |
| Clear | the first run under **80%** of it |

The two fractions are hysteresis: a run under 90% breaks the run of three, and only a run under
80% closes a crossing, so a device sitting on the line is said once and not every period. The
alert carries the same **device-placed writer sample** a fill alert carries — the top writers by
`/proc/<pid>/io` traffic, placed on the mount by device id and never by a command line — and the
highest figure the crossing reached. Nothing here reads the disk: two `/proc` reads a run, which
is why the sample is taken every run and not only while something is wrong.

`--iops-cap MOUNT=IOPS` is repeatable and adds to the default table (`/mnt/durable=7500`);
`--iops-cap /mnt/durable=0` stops watching that mount's IOPS. Counters, the consecutive-run
count and the open crossing live in the state file under `iops`.

**The cap used is the one the disk has now**, read from the cloud with the box's own identity,
and the `--iops-cap` number is only the fallback. On sami-devbox at 2026-11-03T11:22Z this watch
said `/mnt/durable (nvme44n1) 6253 IOPS, 208% of its 3000 cap` while `sami-devbox-cold` had been
raised to 12,000 IOPS for a copy; the 3000 was the installer drop-in's fixed number. Every
5 minutes (`iops_caps_live` in the state file), in a forked child with a 20 s limit, after the
df path:

| Cloud | How the device is matched | Where the cap comes from |
|---|---|---|
| Google Cloud | `/dev/disk/by-id/google-<device name>` → the instance's disk with that `deviceName` | `provisionedIops` and `provisionedThroughput` of that disk, from the Compute API with the metadata server's token |
| Azure | `/dev/disk/azure/data/by-lun`, or NVMe namespace `nvme0n<LUN+2>` | the instance metadata's data disk at that LUN: a `Premium_LRS` disk's size tier (P50 = 7,500 IOPS, 250 MB/s). A performance tier raised above the size's own is not visible this way |

Measured 2026-11-03: sami-devbox `/mnt/durable` 12,000 IOPS, 600 MB/s (`sami-devbox-cold`,
hyperdisk-balanced), `/mnt/nvme` 160,000 IOPS, 2,400 MB/s (`sami-devbox-state`); sami-rocqperf
`/mnt/durable` 7,500 IOPS, 250 MB/s (`sami-rocqperf-durable-1`, P50). Local SSDs, md arrays and
machines off the cloud have no provisioned cap and keep the configured number quietly; a read
that fails keeps it too and says so once (`using the fixed --iops-cap …`). A live read older
than an hour is not trusted over the configured number. Throughput is read and logged; only
IOPS alerts.

## The scratch rule: `/tmp` and `~/src`

Both are shared by every session: agents' scratch in `/tmp` was the repeated root cause on
2026-10-26, and a checkout cut under `~/src` instead of on `/mnt/nvme` fills the root disk. A
top-level entry of either root (`--tmp`, `--src`, default `~/src`) born in the last 6 hours
and now at or over the size bar is named **once**, with the session of a process whose cwd or
open descriptor is inside it. If none is observed, the notice says exactly that and gives the
newest file or directory modification time it saw; it never infers that a session has ended.

**The bar is 2 GB on the root disk** (`--root-big`) and 500 MB elsewhere (`--tmp-big`). A full
root stops every session on the box at once, so a tree there is worth a message only when it is
worth a reader's minute: of the 62 NEW BIG notices in the 48 h to 2026-11-03, those between
0.5 and 2 GB named trees nobody acted on.

**"Born" is the entry's birth, not its ctime.** On 2026-10-31T21:51:59Z this watch named
`~/src/<task repo>` — the shared canonical checkout, created 2026-10-14T01:48:07Z
(`stat -c %w`) — as "born in the last 6 h", because a session had restored its HEAD at
18:01:03Z and a Linux ctime moves with the directory's mtime. The birth now comes from
**statx(2)** (`os.stat` grew `st_birthtime` on Linux only in Python 3.13; this box runs 3.12.3,
so the syscall is made directly, with `stat -c %W` as the fallback). A filesystem that records
no birth answers 0, and **an entry whose birth is unknown is not young**: a rule that cannot
tell an hour-old tree from a three-week-old one stays quiet.

The two differ only in what the message tells the reader to do. A `/tmp` entry: remove it when
the work is done, move it to `/mnt/nvme` while it runs. A `~/src` entry: a checkout belongs on
`/mnt/nvme` — cut it with `agent-workspace new` or `work-dir` — and a tree moved there can leave
a **symlink** at its old path. **A symlink is measured as itself and never followed**, so the
trees moved out of `~/src` on 2026-10-31 with links left behind (61 of them) say nothing; only
a real new tree does.

**Which disk a root is on is read from its device, never assumed.** The root's `st_dev` is
matched to the watched mount with the same device, and the message names that mount and the
device behind it (from `/proc/self/mounts`). Only a root on the same device as `/` gets the
advice above. On sami-rocqperf `~/src` is on `/`, and it does. On sami-devbox `/home` is a bind
of `/mnt/nvme/home`, so `~/src` is on the state disk: on 2026-11-02T19:42:14Z this watch told a
session to move a 4.8 GB checkout there off `/`, where it never was. A root on another watched
disk is now named only when that disk is short (the same short-disk rule below), with advice to
remove it or move it off the box; while that disk has room, a big tree on it is where it
belongs and nothing is said.

Unlike the hand-run script this replaces, a young **directory** is sized too, under a few seconds
of budget per run (`--tmp-idle-walk-budget`, 5 s; `--tmp-walk-budget`, 20 s while building an
alert). That script sized only non-directories, which is how `/tmp/rev1` was named at 17 GB while
the directories beside it never were. What a run's budget does not reach, the next run reaches:
an entry already named is never looked at again. A mount point under either root (containerd's
and buildkit's overlays) holds another filesystem's bytes and is not counted.

**A per-entry notice goes to the entry's own live owner, and to nobody else.** The entry
belongs to one session's work and that session is the only reader who can remove it: on
2026-10-31 the box-health owner received notices naming other sessions (`01a1809b` on
ossv-agent-open, `01a18c07` on open5) that it could do nothing with, and of the 62 notices in
the 48 h to 2026-11-03 it acted on none (dispatch://INFRA-672). The session id read off a
process is a claim, so it is used as an address only when Envoy lists it as live on **this**
box, the same check this watch makes for a writer.

**An entry with no live owner here waits for one digest a day** (`--tmp-digest`, 86,400 s):
one message, one line per entry in its report, whatever each entry's size and however short
the disk is. Nobody can act on an orphan one at a time, and a list of them is the thing worth
reading. An owner that refuses the send puts its entry in that digest too. The window opens at
the first entry, not at the epoch, so the first one is not its own digest, and entries
accumulate in the state file (`tmp_pending`, capped at `--tmp-pending-max`), so a quiet window
loses none.

A **short disk** — free under `max(5% of the filesystem, 100 GB)` — no longer moves a notice
away from its owner; it decides only whether a root OFF the root disk is scanned at all, and
which advice the notice carries. (At a flat 15%, ossv-agent-open2 with 412 GB free of 3.9 TB
counted as short and every entry on it went to the box-health owner, Sami, 2026-10-31; the
fraction with a floor under it is what fixed that.) A short disk is the crossing message's
business, and that message names the writers.

Box-level crossings — the floor, the fill rate, the clear — are about the box and are
unchanged: they always go to the box-health owner.

## The growth watch's own faults

`guard-disk-writers` is the root unit that attributes disk GROWTH to a session over an hour.
It writes only to the journal and cannot send over Envoy, and one event must have one source,
so **this watch forwards the three lines its self-test writes** (chosen by sami-proxy (option
B), 2026-11-03; agent-infra branch `guard-disk-writers-root-selftest`):

| Line | What it means |
|---|---|
| `disk-writers: WATCH UNPROVEN on <mount>: …` | growth on that mount may go unreported; its floor is still checked |
| `disk-writers: WATCH BACK on <mount>: …` | the probe sees writes there again |
| `disk-writers: WATCH STOPPED: …` | the watch is not running and systemd will not start it again |

Read with `sudo -n journalctl -u guard-disk-writers.service -o cat --after-cursor <saved>
--show-cursor`; the cursor lives in this watch's state file under `guard_watch`, so each line
is forwarded **once**. The first run records the cursor from the last 30 minutes, which is one
round of the self-test. After the reporter restarts it writes no per-mount BACK, only
`disk-writers: the self-test passed: …`; while a mount is open from an UNPROVEN, that line is
its BACK, for every open mount. A journal read this watch cannot do is itself said once per
reason, not swallowed: if it were, a fault in the growth watch would reach nobody. A **timeout**
is the exception, because it is a slow box rather than a broken read: at 2026-11-03T04:59:08Z,
load ~320, one 15 s read ran out and the box-health lead was told the journal was unreadable.
A timed-out read is retried once with 45 s inside the run budget, and is said only after two
runs in a row could not read it; sudo refused, a missing unit or a non-zero exit is said at
once. Expected volume: about 0 a day. `--sudo never` turns the forwarding off with the
privileged sample.

## Who hears about it

Agent sessions only, never a person. A crossing goes to the Envoy role
**`notifications.role.lead-box-health`**: the first alert, each repeat a new fact earns, and
the clear. Box health is the box-health lane's work, decided 2026-10-31T02:37:50Z; the chief
of staff was on the escalation until then, the INFRA lead owned this detector and handed the
lane over, and neither is on anything here now.

Nobody holds the role? The publish answers non-200 and the **box-health SRE**
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) hears it instead — every message, first alert,
repeat and clear alike.

A machine whose box health belongs to one session sets `BOX_HEALTH_OWNER` to that session id in
the service's environment: every message then goes to that session alone, with no role publish —
this is how the OSS Verify lane VMs (`ossv-agent-*`) send their alerts to the fleet monitoring
owner instead of to this box's role holder. An invalid value stops the watch at start.

A **NEW BIG entry** goes to its own live owner session and to nobody else; one with no live
owner here is a line in the daily digest, which goes to the role. The clear of a crossing goes
to everyone who heard the crossing.

An IOPS crossing takes the same route: one alert when the device has been at its cap for three
consecutive runs, and one clear when it falls under 80% of the cap. It never repeats — the
cap is a fixed number, so there is no further level to report.

**Expected volume on sami-rocqperf**, against the 48 h measured for dispatch://INFRA-672
(150 messages, 454 k characters):

| | Before, per day | After, per day |
|---|---|---|
| Crossings (first alerts and repeats) | ~35 | ~5-8 |
| Clears | ~9 | ~9, one line each |
| NEW BIG entries | ~31 | ~1 digest, plus whatever reaches an owner session |
| Growth-watch faults (new) | — | ~0 |
| Characters to the box-health lead | ~227 k | ~5-8 k, with the detail in report files |

What this gives up: a slow steady fall below the floor is said less often — between two
messages the disk may lose up to a quarter of what was left, unless the hour runs out first —
and an orphan scratch tree waits up to 24 h unless a crossing fires, where the crossing
message already names the top writers.

Every alert ends with what to DO: act only on the writers listed as on this filesystem (stopping
one from the elsewhere group frees nothing here), stop or finish that lane, move its output to
the other disk, then reclaim with skill://disk-hygiene — whose check-in comes
first and which never deletes a directory a live session stands in. **This watch only detects. It
deletes nothing, kills nothing and moves nothing.**

## One alert per crossing

`/mnt/nvme/sami/tmp/disk-fill-watch/state.json` is the only memory, and the service owns it (a
machine without `/mnt/nvme/sami/tmp` uses `$XDG_STATE_HOME/disk-fill-watch/state.json`):

- held under an exclusive `flock` for the whole run, so a hand run and the timer can never both
  open the same crossing; the loser logs `another run holds …` and skips its period;
- `alerted_at` is written and `fsync`ed **before** the first message is sent;
- it also holds the df free-space samples, each mount's in-progress consecutive rate
  confirmations, the free space each open crossing was last reported at (`reported_free`, what
  the 25% fall is measured from) and whether that message was under the hour
  (`reported_under_hour`), the `/tmp` and `~/src` entries already named, where the last scratch
  scan of each root stopped (`scan_cursor`), the growth watch's journal cursor and open mounts
  (`guard_watch`), and each capped mount's last `/proc/diskstats` counters under `iops`;
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run`.

## Inputs

| Input | Source |
|---|---|
| Free and total space | `os.statvfs(mount)` — `f_bavail`, `f_blocks`, `f_frsize` |
| df free-space fill rate and time to full | this watch's own free-space samples in its state file |
| Writers | `/proc/<pid>/io` `write_bytes`, twice, `--writer-sample` apart, through `sudo -n` where allowed; reported as single-window write traffic |
| Disk growth kept, by session | `sudo -n journalctl -u guard-disk-writers.service --since -60min -o cat`, the guard's `growth session:` rows for the crossing mount; best-effort, capped at 15 s |
| The growth watch's own faults | `sudo -n journalctl -u guard-disk-writers.service -o cat --after-cursor <saved> --show-cursor`, its `disk-writers: WATCH …` lines |
| A writer's session | `/proc/<pid>/environ`, `/proc/<pid>/cmdline`, `/proc/<pid>/fd` → `~/.omp/agent/sessions/**/*.jsonl`, then its ancestors |
| A writer's mount | `st_dev` of `/proc/<pid>/cwd` and of each regular file or directory open on `/proc/<pid>/fd/N`, stat'd through the fd, with the access mode from `/proc/<pid>/fdinfo/<N>`, compared with the mount's `st_dev` at the sample instant |
| Young `/tmp` and `~/src` entries | `os.scandir` of each root, a budgeted `os.walk` for directories (symlinks measured as themselves, never followed), and the newest file or directory mtime seen while sizing |
| IOPS of a capped device | reads completed + writes completed from `/proc/diskstats`, for the major and minor numbers of the mount's `st_dev`, against the previous run's reading |
| Listener | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Samples, confirmations, crossings, named entries, scan cursors, the guard cursor | `/mnt/nvme/sami/tmp/disk-fill-watch/state.json` |
| Report files | `/mnt/nvme/sami/tmp/disk-fill-watch/reports/`, kept 7 days |

## Running it by hand

```sh
scripts/disk-fill-watch --report                                     # the newest report in full
scripts/disk-fill-watch --dry-run --print-alert                      # decide and print, send nothing
scripts/disk-fill-watch --state /tmp/x.json --write-state \
    --print-alert --grace 0 --floor '/=400' --max-floor-fraction 0.9 \
    --mount /                                                        # deliberate trigger, sends nothing
scripts/disk-fill-watch --state /tmp/x.json --write-state --sudo never \
    --iops-cap '/mnt/durable=7500'                                   # the IOPS line, twice for a figure
```

A message names its own report; `--report` prints the newest one whatever sent it. A run on a
state file other than the default forces `--dry-run`, and still writes its reports beside that
file, so a hand run shows exactly what would have been sent and what the reader would open.

## Turning it off

```sh
systemctl --user disable --now disk-fill-watch.timer   # stop it
systemctl --user enable  --now disk-fill-watch.timer   # start it again
systemctl --user list-timers disk-fill-watch.timer     # when it next runs
journalctl --user -u disk-fill-watch.service -n 50     # what the last runs decided
```

`installers/disk-fill-watch.sh` arms it (links `omp/disk-fill-watch.{service,timer}` into
`~/.config/systemd/user`). Deleting the state file forgets every open crossing and every df rate:
the next run starts sampling again, re-alerts on a floor that is still crossed, and needs both
`--min-span` of samples and three spaced rate confirmations before it can alert on time to full.
