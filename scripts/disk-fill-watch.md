# disk-fill-watch

Every 5 minutes, records free space on each watched filesystem of **this** machine and computes
the fill rate from those `df`/`statvfs` samples. It alerts over Envoy when the disk is about to
land — either because free space is under a hard floor, or because **three consecutive scheduled
five-minute samples** each put time to full at the df free-space fill rate under the horizon.

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
| Alert | after `--grace` (240 s, so a second consecutive run confirms it) |
| Escalate | 30 minutes after the alert, if free is lower than it was then |
| Clear | free ≥ floor × 1.5 **and** no df free-space fall that reaches full inside twice the horizon |

**A sawtooth is not a leak.** `/mnt/nvme` on this box swings about 100 GB up and down every hour
as grade jobs land and are cleaned (its 10-minute log on 2026-10-26 reads 295 GB → 479 → 378 →
340 → 232 free), so an hour of negative slope on its own fires on every downstroke. The new-low
test is what keeps it quiet: only a fall to a level the last hour has not seen is a fall towards
zero.

A one-minute fall is recorded, not sent. Three scheduled five-minute confirmations span at least
ten minutes, so a one-off writer burst that has already recovered cannot claim that the disk is
landing. The hard floor remains independent of this confirmation: below it the watch alerts
regardless of rate.

### The floors

| Mount | Floor | Why |
|---|---|---|
| `/` | 18 GiB | Root holds every session's transcript, the journal, apt/dpkg state and `/tmp`, so a full root stops the whole box, not one lane. Root's band on 2026-10-26 afternoon was 25–28 GiB, so 25 would have paged all day; 18 leaves room to act |
| `/mnt/nvme` | 84 GB (decimal) | The grade farm's build-cache floor — binary-equivalence-taiga farm `e1417ce`, `disk_floor.sh` `FETCH_KIND_BOUND` and `farm_build.py` `PREPARE_FLOOR_BYTES` — the level at which a registration build refuses to start, so it is where real work stops |
| anything else | `--default-floor`, 20 GiB | |

An alert on `/mnt/nvme` also names every grade-farm transfer kind whose own floor the current
free space is under (grading-tools `grade_farm/floors.py` as of #1238: `generic-unmeasured` 150 GB,
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
15-second sample**, the pid and its age, the **owning agent session**, the working directory when
it is on the alerting mount, and the largest file it holds open there.

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
  time to full. The cwd and open file only tie a writer to the mount at the sample instant.
- Another user's `/proc/<pid>/io` is refused to an unprivileged reader, and root owns the writers
  that failed first on 2026-10-27 (rsyslogd on `/var/log/syslog`, dockerd's `/tmp` exec scratch).
  So where `sudo -n` is allowed the whole sample — both `/proc` passes and the cwd and fd reads —
  runs as root in a child of this script (`--writers-json`, which reads `/proc` and prints JSON;
  it sends nothing and writes nothing). Where it is not, the unprivileged sample is used and the
  alert says how many processes refused the read, so root's writers are never reported as absent.
  `--sudo never` turns the privileged half off.

The expensive reads happen only on a run that actually sends. An ordinary run is `statvfs` plus a
bounded `/tmp` scan: **0.3–0.4 s** measured on this box at load 43.

## The /tmp rule

`/tmp` is on `/` here and agents' scratch there was the repeated root cause on 2026-10-26. A
top-level `/tmp` entry born in the last 6 hours and now at or over 500 MB is named **once**, with
the session of a process whose cwd or open descriptor is inside it. If none is observed, the
alert says exactly that and gives the newest file or directory modification time it saw; it never
infers that a session has ended.

Unlike the hand-run script this replaces, a young **directory** is sized too, under a few seconds
of budget per run (`--tmp-idle-walk-budget`, 5 s; `--tmp-walk-budget`, 20 s while building an
alert). That script sized only non-directories, which is how `/tmp/rev1` was named at 17 GB while
the directories beside it never were. What a run's budget does not reach, the next run reaches:
an entry already named is never looked at again.

**A per-entry notice goes to the entry's own live owner**, not to the box-health owner. The
entry belongs to one session's work and that session is the only reader who can remove it: on
2026-10-31 the box-health owner received notices naming other sessions (`01a1809b` on
ossv-agent-open, `01a18c07` on open5) that it could do nothing with. The session id read off a
process is a claim, so it is used as an address only when Envoy lists it as live on **this**
box, the same check this watch makes for a writer.

Three cases take a notice away from the lane, and each says which it is:

- **the disk is short.** Not a flat fraction: free under `max(5% of the filesystem, 100 GB)`.
  At a flat 15%, ossv-agent-open2 with 412 GB free of 3.9 TB counted as short and every entry
  on it went to the box-health owner (Sami, 2026-10-31) — 412 GB is more headroom than most of
  these boxes ever have. The fraction catches a small disk; the floor stops a big one reading
  as short while it still holds more free space than a whole lane VM's root. A short disk's
  entry goes to the box-health owner at once, whatever its size and whoever owns it.
- **no live session on this box matches the entry** (none observed, a `pid N (comm)` answer, or
  an id live somewhere else) **and it is at or over `--tmp-digest-max`** (2 GB): a message of
  its own to the box-health owner.
- **unowned and under `--tmp-digest-max`**: one line in the **hourly digest**
  (`--tmp-digest`, 3600 s), never a message of its own. Nobody can act on these one at a time,
  and a message each was all the box-health owner got from open2; as a list they still say
  something, so they are batched rather than dropped. The window opens at the first entry, not
  at the epoch, so the first one is not its own digest. Entries accumulate in the state file
  (`tmp_pending`, capped at `--tmp-pending-max`), so a quiet window loses none.

An owner that refuses the send gets the notice resent to the box-health owner rather than
dropped. Box-level crossings — the floor, the fill rate, the clear — are about the box and are
unchanged: they always go to the box-health owner.

## Who hears about it

Agent sessions only, never a person. Every message goes to the Envoy role
**`notifications.role.lead-box-health`**: the first alert, the escalation 30 minutes later when
a crossing is still open and free space is still **lower than at the alert**, and the clear.
Box health is the box-health lane's work, decided 2026-10-31T02:37:50Z; the chief of staff was
on the escalation until then, the INFRA lead owned this detector and handed the lane over, and
neither is on anything here now.

Nobody holds the role? The publish answers non-200 and the **box-health SRE**
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) hears it instead — every message, first alert,
escalation and clear alike.

A machine whose box health belongs to one session sets `BOX_HEALTH_OWNER` to that session id in
the service's environment: every message then goes to that session alone, with no role publish —
this is how the OSS Verify lane VMs (`ossv-agent-*`) send their alerts to the fleet monitoring
owner instead of to this box's role holder. An invalid value stops the watch at start.

One alert per crossing, one clear. The clear goes to everyone who heard the crossing.

Every alert ends with what to DO: read the writers first, stop or finish the lane that is writing,
move its output to the other disk, then reclaim with skill://disk-hygiene — whose check-in comes
first and which never deletes a directory a live session stands in. **This watch only detects. It
deletes nothing, kills nothing and moves nothing.**

## One alert per crossing

`/mnt/nvme/sami/tmp/disk-fill-watch/state.json` is the only memory, and the service owns it (a
machine without `/mnt/nvme/sami/tmp` uses `$XDG_STATE_HOME/disk-fill-watch/state.json`):

- held under an exclusive `flock` for the whole run, so a hand run and the timer can never both
  open the same crossing; the loser logs `another run holds …` and skips its period;
- `alerted_at` is written and `fsync`ed **before** the first message is sent;
- it also holds the df free-space samples, each mount's in-progress consecutive rate
  confirmations, and the `/tmp` entries already named;
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run`.

## Inputs

| Input | Source |
|---|---|
| Free and total space | `os.statvfs(mount)` — `f_bavail`, `f_blocks`, `f_frsize` |
| df free-space fill rate and time to full | this watch's own free-space samples in its state file |
| Writers | `/proc/<pid>/io` `write_bytes`, twice, `--writer-sample` apart, through `sudo -n` where allowed; reported as single-window write traffic |
| A writer's session | `/proc/<pid>/environ`, `/proc/<pid>/cmdline`, `/proc/<pid>/fd` → `~/.omp/agent/sessions/**/*.jsonl`, then its ancestors |
| A writer's mount | `/proc/<pid>/cwd` and `/proc/<pid>/fd`, compared with the mount's `st_dev` at the sample instant |
| Young `/tmp` entries | `os.scandir('/tmp')`, a budgeted `os.walk` for directories, and the newest file or directory mtime seen while sizing |
| Listener | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Samples, confirmations, crossings, named `/tmp` entries | `/mnt/nvme/sami/tmp/disk-fill-watch/state.json` |

## Running it by hand

```sh
scripts/disk-fill-watch --dry-run --print-alert                      # decide and print, send nothing
scripts/disk-fill-watch --state /tmp/x.json --write-state \
    --print-alert --grace 0 --floor '/=400' --mount /                # deliberate trigger, sends nothing
scripts/disk-fill-watch --mount / --mount /mnt/nvme --horizon 7200   # the unit's defaults
```

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
