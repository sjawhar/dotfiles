# session-scratch-reap

One script, two roots on `/mnt/nvme/<user>`, two rules:

| Root | Run by | Rule |
|---|---|---|
| `session-tmp/` | `devbox/box-cleanup`, every pass (15 minutes) | each agent session's own `TMPDIR`, made by `scripts/agent-session`; removed once no live process holds it |
| `tmp/` | `devbox/box-cleanup`'s `session-tmp-reap` step, daily, **report-only** | the working directories agents make by hand; an entry is removed only when its `OWNER` file names a session that is gone, nothing holds it, and nothing in it was written for 7 days |

Everything down to "The tmp root" is about `session-tmp/`.

On sami-devbox, where `devbox/gce-disks` mounts the local SSDs at `/mnt/scratch` and makes
`/mnt/scratch/<user>`, the `session-tmp/` root is `/mnt/scratch/<user>/session-tmp` instead
(`scripts/agent-session` and this script both prefer it when it exists); `tmp/`, where
`scripts/work-dir` makes directories meant to outlive a session, stays on `/mnt/nvme/<user>`.

Every agent session on a machine with `/mnt/nvme/<user>` gets a **TMPDIR of its own** there, and
this reaper removes it when the session is gone. Two halves, two files:

| Half | File | What it does |
|---|---|---|
| Create | `scripts/agent-session` | `mktemp -d /mnt/nvme/<user>/session-tmp/$$-XXXXXXXX`, exported as `TMPDIR` **and** `AGENT_SESSION_SCRATCH`, before it execs the agent |
| Remove | `devbox/session-scratch-reap` | on every `box-cleanup` pass, removes a directory there that no live process holds |

## Why it exists

On 2026-10-27 the root filesystem of sami-rocqperf filled to ENOSPC over 5h41m and wedged the box
(rsyslog, journald and dockerd's exec all failed). No single writer did it: 10,952 agent tool
scopes between 18:00Z and 00:01Z — 990 `go build`/`test`, 598 `uv run`, 427 `pytest`, 11 clones —
wrote their scratch and caches into a root-resident `/tmp`, `~/.cache` and `~/.cargo`. Root is
also where every session's transcript lives, so a full root stops every session at once.
(dispatch://INFRA-613)

`$$` in `agent-session` is the agent's own pid: every hop from there to the agent is an `exec`,
which is also what the secrets broker's helper relies on. So the directory name carries its
owner.

## Why a timer and not an exit hook

There is no per-session end hook on this box to hang the removal on:

- **A systemd scope refuses one.** `shims/omp` starts a capped session with `systemd-run --user
  --scope`, and systemd answers `Unknown assignment: ExecStopPost=` (measured 2026-10-27);
  scope units support no `Exec*` at all.
- **`agent-session` cannot wait for the agent.** It `exec`s it, and must: the pid the broker
  registered has to stay the agent's.
- **The agentbox exit path is box-only.** It removes the box's own tree (`<boxes>/<box>`:
  `/mnt/nvme/<user>/agentbox/boxes` here, `~/boxes` on a machine without that disk), and a box
  mounts an allow-list of `$HOME` plus that tree and nothing else of `/mnt`, so a box session
  has no scratch directory under `/mnt/nvme/<user>/tmp` at all.
- **`envoy-role-holder-watch` sees role holders and tmux panes**, not every session, and it
  alerts rather than deleting.

So the reaper is the mechanism, and `devbox/box-cleanup` runs it on every pass, so removal
follows the session's end closely. Run once a day, it met a whole day of dead sessions at once:
the first run of the day ran into box-cleanup's 30-minute step limit on 2026-11-18, 11-19 and
11-20, and the run that started at 23:36Z on 11-20 had removed 449 directories when it was
killed. A pass with little to remove takes seconds (3-15 s on 2026-11-21).

## The liveness test — never age

`skill://disk-hygiene`: age never decides that a path may be deleted; it may only keep one
longer. One run walks `/proc` once and calls a scratch directory **live** when:

- any process names it (or a path inside it) in `AGENT_SESSION_SCRATCH` or `TMPDIR` — that is the
  session itself, every tool subprocess it started, and anything it detached that is still
  writing there; or
- any process's `cwd` is inside it, or it holds a file open in it; or
- the pid the directory is named for exists and its environment cannot be read (not proof of
  death, so it stays).

Everything else is removed, **except**:

- a directory younger than the grace window (600 s), which is kept whatever `/proc` says: that
  covers the moment between `mkdir` and the agent's `exec`, and a session that has not yet
  written anything;
- a directory holding an entry **another user owns**, at any depth, which is kept whole and named
  (`keep … (it holds another user's file: <path>, uid <n>; …)`) and counted in the summary as
  holding another user's files. A container that ran as root writes root-owned files into the
  `TMPDIR` it was given. This user cannot read that user's `/proc` to see whether it still uses
  them, and cannot unlink them: on sami-devbox, `rm -rf` of `943835-wuvEGAU1` took the user's
  own files and then failed on `tmp.X7DxElRLHB` (root:root, mode 700), so the step failed on
  every box-cleanup pass from 2026-11-21T02:00Z. At 06:28:42Z that day `1951829-1DvYInlG` held a
  root-owned `probe.CoPz/…/verifier`, its session's pid was gone, and its mtime read 06:28:37Z:
  something this user cannot see was still writing there. One walk of each directory about to go
  finds such an entry before anything in it is removed. Removing what is left takes root, once
  its owner is done with it;
- a directory with a **filesystem mounted** at or below it, kept whole and named (`keep … (a
  filesystem is mounted inside it, at <path>; …)`) and counted as holding a mount, in both roots.
  `rm -rf` deletes through a mount: the files of a tmpfs or another disk, or, through a bind
  mount, another directory's. `/proc/self/mountinfo` names every mount point, bind mounts of the
  same filesystem too (`devbox/mounts-below.sh`), and the removal is `rm -rf --one-file-system`
  besides, so a mount the check misses is skipped and that removal fails and is said.

A run that finds `/proc/pressure/io` `full avg10` above 20 removes nothing and says so; the next
pass is 15 minutes away.

## A deadline, and what a run reports

`box-cleanup` gives the reaper `SESSION_SCRATCH_RUN_SECONDS=1500`, inside its 30-minute step
limit. At the deadline the run stops before the next directory, or ends the walk or the `rm -rf`
in progress, and says so (`stopped at its deadline during the removal of <dir>, …; N of M
directories left for the next run`). It exits 0 unless a removal failed: the next pass judges a
half-removed directory again and carries on. A walk the deadline ends before the run removed
anything is a failure, since the next pass would get no further. A hand run has no deadline.

The same walk gives each directory's bytes, so every run ends with `reclaimed-bytes: N`: the
bytes of the directories it removed, which box-cleanup records as the step's `reclaimed`.
`--dry-run` removes nothing and says `reclaimed-bytes: 0`.

## Proof

```
$ ~/.dotfiles/scripts/agent-session bash -c 'echo $TMPDIR; sleep 600' &   # a simulated session
/mnt/nvme/sami/session-tmp/2537981-u9kDJDdQ
$ devbox/session-scratch-reap --dry-run
session-scratch-reap: keep /mnt/nvme/sami/session-tmp/2537981-u9kDJDdQ (live: pid 2537981)
$ kill 2537981
$ SESSION_SCRATCH_GRACE_SECONDS=0 devbox/session-scratch-reap
session-scratch-reap: removed /mnt/nvme/sami/session-tmp/2537981-u9kDJDdQ (no live process, 41s old)
```

## Running it by hand

```sh
devbox/session-scratch-reap --dry-run    # decide and print, remove nothing
journalctl --user -u box-cleanup.service | grep '  session-scratch-reap: '
```

`SESSION_SCRATCH_ROOT`, `SESSION_SCRATCH_GRACE_SECONDS`, `SESSION_SCRATCH_IO_PRESSURE_MAX` and
`SESSION_SCRATCH_RUN_SECONDS` override the defaults, which is how
`scripts/tests/test_session_scratch_reap.py` runs the rules above against a scratch root.

## Rollback

One line turns the removal off: the `session-scratch-reap` entry of `STEPS` in
`devbox/box-cleanup`. Remove it through a pull request, as any change to `~/.dotfiles`.

One command turns the per-session TMPDIR off for every session started after it (sessions keep
the environment they started with), by giving `agent-session` a directory it will not use:

```sh
git -C ~/.dotfiles revert --no-edit <commit>   # the commit named in this file's history
```

…or, without touching the repository, start the session with `TMPDIR` already set: a caller's
value wins, and `AGENT_SESSION_SCRATCH` set to an existing directory makes the session reuse it
instead of making one.

## What happens to a session started the old way

Nothing is removed. The environment is read at launch, so a session already running keeps the
`TMPDIR` it started with (on this box, the shared `/mnt/nvme/sami/tmp`), and the `session-tmp`
root holds only what the new `agent-session` writes into it. The shared `/mnt/nvme/sami/tmp` is
read by the second root below — which, report-only, removes nothing at all, and which even when
live never takes an entry that has no `OWNER` file, so a path other sessions pass each other by
absolute name stays where it is.

One interaction to know: `omp/extensions/decision-review-gate.ts` reads reviews from
`$TMPDIR/verified-linux/reviews`. Its reviewers are `reviewer`/`scout` **tasks**, which run in
the session's own process and so share its `TMPDIR`; a review written by a *different session*
would no longer be found, and must be written to an absolute path both sessions name.

## The tmp root

`/mnt/nvme/<user>/tmp` is the other scratch area on the big disk: the working directories agents
make **by hand**, `<Name>-<session-id>` or a bare name such as `verified-linux/`. Nothing
allocates them, so nothing owned their removal, and the root reached **1,095 GB in 13,657
entries on 2026-10-31** (dispatch://INFRA-675). Keying on the session id in the name would have
freed 11 GB of it: only 23 entries carried one.

So ownership is declared, not guessed. **An entry may hold a file `OWNER` at its top with one
full 36-character session id.** Nothing requires it; a lane that wants its data kept makes its
working directory with `scripts/work-dir`, which writes `OWNER` from `$OMP_SESSION_ID` (omp gives
each tool process its session's id) and prints the path:

```sh
dir=$(work-dir my-work)   # /mnt/nvme/$USER/tmp/my-work, OWNER = this session's id in full
```

| The entry | What happens to it |
|---|---|
| No `OWNER`, an `OWNER` holding a **prefix** (`01a16551-5d7c`), two ids, a comment, or an `OWNER` that is a symlink | **unowned**: never removed, whatever its age. It is counted in every run's summary and named in the weekly report once it passes 1 GB |
| `OWNER` names a session Envoy lists live **on this box** | kept. A paused agent is still a live session, and it resumes expecting its work |
| Envoy does not answer | **every** owner counts as live and nothing is a candidate. A reaper that cannot ask must not conclude the asker is gone |
| A process of this user stands in it, holds a file open in it, or names it in `TMPDIR`/`AGENT_SESSION_SCRATCH` | kept, by the same `/proc` pass the `session-tmp` root uses |
| Anything inside it was written in the last `TMP_REAP_IDLE_DAYS` (7) | kept |
| One metadata walk of the whole root measures that. A path it could not read keeps **that** entry (a container that ran as root leaves files this user cannot read, and a root this size always holds some); a walk that runs past `TMP_REAP_WALK_SECONDS` (2 h) keeps every entry | kept |
| All three hold — no holder, owner gone, idle 7 days | the one case a removal is allowed in |

Symlinks are never followed, a mount under the root is never crossed or acted on (the entry that
is a mount point reads as unowned, and an entry with a mount deeper inside is kept, as in the
session root), and the root itself is never a candidate.

### It is report-only

It is delete-capable, the class of thing that was caught twice in dry run about to kill healthy
sessions (`omp-box-memory-guard.DEFECTS.md`, `git show 2d52488`), so it ships deciding and
printing and removing nothing — **whatever `--dry-run` says**, and whatever the timer does. Each
run ends with one line — this one is the first run on sami-rocqperf, 2026-10-31, where no entry
carried an `OWNER` file yet, so the whole root read as unowned:

```
session-scratch-reap: /mnt/nvme/sami/tmp: report-only (TMP_REAP_LIVE unset): 0 would be removed (0.0 GB), 0 with a live owner (0.0 GB), 0 held by a process (0.0 GB), 0 written inside the 7d window (0.0 GB), 13774 unowned (1491.0 GB); 0 failed; 115G free
```

On Mondays, and on any run given `--report`, it also names the unowned entries over 1 GB,
largest first: that list is the whole of what this root cannot reclaim on its own, and each line
needs either an `OWNER` file from its lane or that lane's own removal.

**Making it live** is one word in the `session-tmp-reap` entry of `STEPS` in
`devbox/box-cleanup`, which runs it, after the review of a week of its decisions passes:
`TMP_REAP_LIVE=1` before the command, beside `TMP_REAP_WALK_SECONDS`. Nothing else changes: the
same run that printed `would remove` prints `removed`.

### Running it by hand

```sh
devbox/session-scratch-reap --tmp              # decide and print
devbox/session-scratch-reap --tmp --report     # …and name the unowned entries over 1 GB
journalctl --user -u box-cleanup.service | grep '  session-tmp-reap: '
```

`TMP_REAP_ROOT`, `TMP_REAP_IDLE_DAYS`, `TMP_REAP_WALK_SECONDS`, `TMP_REAP_ENVOY_SECONDS`,
`TMP_REAP_REPORT_MIN_BYTES`, `ENVOY_URL` and `TMP_REAP_LIVE` override the defaults, which is how
`scripts/tests/test_session_tmp_reap.py` runs every rule above against a scratch root and a fake
Envoy. Removing its entry from `STEPS` in `devbox/box-cleanup` turns the whole root off.

It runs **daily**, not on every pass like the `session-tmp` root: one run walks the metadata of
the whole root to find the newest write inside each entry, and nothing there appears or
disappears in fifteen minutes.
