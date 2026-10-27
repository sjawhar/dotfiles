# session-scratch-reap

Every agent session on a machine with `/mnt/nvme/<user>` gets a **TMPDIR of its own** there, and
this timer removes it when the session is gone. Two halves, two files:

| Half | File | What it does |
|---|---|---|
| Create | `scripts/agent-session` | `mktemp -d /mnt/nvme/<user>/session-tmp/$$-XXXXXXXX`, exported as `TMPDIR` **and** `AGENT_SESSION_SCRATCH`, before it execs the agent |
| Remove | `devbox/session-scratch-reap` | every 2 minutes, removes a directory there that no live process holds |

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
- **The agentbox exit path is box-only.** It removes `~/boxes/<box>`, and a box mounts an
  allow-list of `$HOME` with no `/mnt` in it, so a box session has no scratch directory here at
  all.
- **`envoy-role-holder-watch` sees role holders and tmux panes**, not every session, and it
  alerts rather than deleting.

So the reaper is the mechanism, and it runs every 2 minutes so removal follows the session's end
closely.

## The liveness test — never age

`skill://disk-hygiene`: age never decides that a path may be deleted; it may only keep one
longer. One run walks `/proc` once and calls a scratch directory **live** when:

- any process names it (or a path inside it) in `AGENT_SESSION_SCRATCH` or `TMPDIR` — that is the
  session itself, every tool subprocess it started, and anything it detached that is still
  writing there; or
- any process's `cwd` is inside it; or
- the pid the directory is named for exists and its environment cannot be read (not proof of
  death, so it stays).

Everything else is removed, **except** a directory younger than the grace window (600 s), which
is kept whatever `/proc` says: that covers the moment between `mkdir` and the agent's `exec`, and
a session that has not yet written anything.

A run that finds `/proc/pressure/io` `full avg10` above 20 removes nothing and says so; the next
run is two minutes away.

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
journalctl --user -u session-scratch-reap.service -n 50
```

`SESSION_SCRATCH_ROOT`, `SESSION_SCRATCH_GRACE_SECONDS` and `SESSION_SCRATCH_IO_PRESSURE_MAX`
override the defaults for a test.

## Rollback

One command turns the removal off:

```sh
systemctl --user disable --now session-scratch-reap.timer
```

One command turns the per-session TMPDIR off for every session started after it (sessions keep
the environment they started with), by giving `agent-session` a directory it will not use:

```sh
git -C ~/.dotfiles revert --no-edit <commit>   # the commit named in this file's history
```

…or, without touching the repository, start the session with `TMPDIR` already set: a caller's
value wins, and `AGENT_SESSION_SCRATCH` set to an existing directory makes the session reuse it
instead of making one.

## What happens to a session started the old way

Nothing. The environment is read at launch, so a session already running keeps the `TMPDIR` it
started with (on this box, the shared `/mnt/nvme/sami/tmp`), and the reaper only ever looks
inside `/mnt/nvme/<user>/session-tmp`, which only the new `agent-session` writes into. The shared
`/mnt/nvme/sami/tmp` and everything in it — including the paths other sessions pass each other by
absolute name — is never touched.

One interaction to know: `omp/extensions/decision-review-gate.ts` reads reviews from
`$TMPDIR/verified-linux/reviews`. Its reviewers are `reviewer`/`scout` **tasks**, which run in
the session's own process and so share its `TMPDIR`; a review written by a *different session*
would no longer be found, and must be written to an absolute path both sessions name.
