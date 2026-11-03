# omp-dup-watch

Says when one omp session id runs in more than one live omp process on this box — a second copy
in another tmux server, in an agent box, or started by hand. It only reports: it ends no process
and changes no session.

Armed by `installers/omp-dup-watch.sh` as the `omp-dup-watch` user timer
(`omp/omp-dup-watch.{service,timer}`), every 5 minutes.

```sh
omp-dup-watch --report                 # every duplicated id now, with its processes
omp-dup-watch --dry-run                # decide and print the message and the report; send nothing
omp-dup-watch --print-alert            # print each message as well as sending it
```

## The incident it is built from

On sami-rocqperf, 2026-11-03 00:29:44-01:08:51Z: a session ran `tmux new-session -d` without
`TMUX_TMPDIR`, which started a second tmux server at `/tmp/tmux-1000/default`. tmux-resurrect
restored the saved layout on it, because its gate matched only the socket name `default`, and
`tmux-resurrect-omp` then resumed every saved session. **36 sessions got a second omp** in that
server's panes, and **14 boxed sessions got one in a new agent box each** (boxes created
00:30:59-00:32:13Z). Two omps on one session append to one transcript, and Envoy's registry is
keyed by session id, so only the copy that heartbeated last receives the session's messages. The
duplicates also took memory: 723 of the window's 759 earlyoom kills fell in that hour.

Nothing on the box watched for it. A person found it at about 00:47Z, 17 minutes in
(`tick-0100-final.md`). `~/.dotfiles/scripts` had no detector; `omp-session-live` answers the
same question for **one** id, and only when something asks it.

Both paths are fixed at the source: dotfiles 20f7a971 restores a snapshot only on the workspace
server, and f007ab4 made `agent-session` and `agentbox new` ask `omp-session-live` before any
`omp --resume`, so a session whose omp is live is not started again. This watch is the detector
for the next way it happens.

## What one run does

1. **One pass over /proc**: each process's `stat` (name, state, parent, start time). 3,639
   processes, 0.13 s at load 277.
2. **Each omp that can run a session**: its `cmdline`, `cgroup`, `cwd`, open files and `TMUX`.
   `__omp_worker_*` processes run the same binary and are omp's own helpers, so they are
   counted and skipped. A pid whose `stat` no longer reads `omp` with the same start time when
   those files have been read was given to another program in between, and is dropped: at
   02:59Z on 2026-11-03 pid 1728661 was `omp` in the pass and a python runner a second later.
3. **An agent box's omp** is in this same /proc: it shows `docker-<id>.scope` in its cgroup, and
   `docker ps` gives the box name. It runs under the box's uid, so its descriptors, working
   directory and environment are not readable from the host; its command line is.
4. **Its session id**, by the rules `tmux-resurrect-omp` and `agentbox`'s move census use:

   | Source | Rule |
   | --- | --- |
   | the transcript it holds open **for writing** | `…/sessions/…/<stamp>Z_<id>.jsonl` in its `fd` table whose `fdinfo` flags carry `O_WRONLY` or `O_RDWR`, newest first; a transcript the sweep deleted still names the session |
   | this watch's own last run | the same pid and start time, seen holding that transcript: omp opens the file only to write, so an idle session closes nothing but keeps running |
   | its own command line | `--resume`/`--restore`/`-r` with a transcript path, a full id, or the id prefix omp accepts |

   Only a writer runs a transcript. omp's session writer holds its own file `O_WRONLY|O_APPEND`
   (flags `02102001` on every writer here on 2026-11-03), and an omp opens ANOTHER session's
   transcript read-only when a tool reads it. At 05:59:13Z the first live version of this watch
   reported session `01a170e0-e386` as run by two omps: its own, and omp 2559955, which runs
   `01a170df-c9fd` and held `01a170e0`'s transcript read-only for a moment. That file was the
   newer one, so it won the "newest first" rule. A read-only descriptor now names no session.

   The id must come from the file **name**, at any depth: three live omps here resume
   `…/<bucket>/<stamp>_<other id>/<stamp>_<id>.jsonl`, while a subagent's transcript
   (`…/<stamp>_<id>/ArityLane.jsonl`) names no session — two subagents of one session must never
   read as two copies of it. A prefix is matched to a full id only when exactly one id fits.
5. **Envoy**: `GET /v1/sessions`, kept to the rows whose `host` is this machine or one of its
   boxes.

An id with **more than one process**, or more than one Envoy row, is duplicated.

## What it says, and when

- **One message per run** holds every id that is duplicated and was not duplicated at the last
  run. An id that stays duplicated is not said again: the first message asked for the only
  action there is.
- **One line when an id clears** (back to one omp, or none).
- Both are at most **600 characters** and end with `Full report: <path>`. The report
  (`<state dir>/reports/<UTC stamp>-duplicate.txt`) holds, for every duplicated id: each
  process's pid, start time, tmux server and socket and pane or agent box, working directory,
  how its id was read and its command line; Envoy's row and which copy it delivers to; and what
  to do. Reports older than 7 days are deleted in the same run.
- A message no Envoy route accepted leaves the id unopened, so the next run says it again.

## Where notices go

The same route as every box watch here (`load-swap-watch.md`, "Where alerts go"):
`BOX_HEALTH_OWNER` when the service's environment sets it, else the box-health lead's role
(`notifications.role.lead-box-health`), else the SRE session
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`).

## Cost, and the budget

Measured on sami-rocqperf, 2026-11-03 02:59Z, load 277, 3,639 processes, 87 omp processes
(73 sessions-capable, 14 helpers), 68 Envoy registrations for this host and its boxes:
**1.15 s** for the whole run, 0.9 s of it the census. `agentbox`'s full move census took 56-221 s
at load 300 because it infers each session's paths; this one reads /proc once and asks nothing
else about a process.

`--budget` (45 s) bounds the census. A census cut short alerts on what it found and **clears
nothing**, because an id it did not reach would otherwise read as cleared. The unit's
`TimeoutStartSec` is 120 s, which only `docker ps` or Envoy hanging can reach.

## Expected volume, and what it gives up

**0 messages a day** while nothing is duplicated (the state on 2026-11-03 02:59Z: 0 duplicated
ids). An incident like 2026-11-03's sends **2**: one alert naming every id made in that burst,
one clear. A duplicate made in two bursts sends one alert each.

What that gives up: an id that stays duplicated for hours is said once, so a message read and
forgotten is not repeated — `omp-dup-watch --report` lists what is still open, and the state
file holds each id's `opened_at`. An id this watch cannot name (`omp -p --no-session`, a session
that has not written its transcript and carries no `--resume`: 9 of 73 processes at 02:59Z) is
counted in the report's census line and cannot be found duplicated.

## What it will not catch

- **A duplicate on another machine.** Only this box's /proc and this box's Envoy rows are read;
  a session running here and on sami-leanperf is `agentbox move --to`'s own refusal
  (`move_to_preflight`), not this watch.
- **Two Envoy registrations of one id.** The listener keys its session bucket by session id
  (`packages/envoy/internal/session/registry.go`, `PutWithin` → `mergeForClaim`), so one id has
  one row and the count is a cross-check that should never fire. What the row does say is which
  copy now receives the session's messages, and the report names it.
- **A second omp that is not a session**: `omp -p` runs, which hold no transcript and resume
  nothing, are counted as unidentified rather than attributed to a session.
- **Its own silence.** `unit-health-watch` reports a unit whose runs stop completing.

## Turning it off

`systemctl --user disable --now omp-dup-watch.timer`; nothing re-arms it until someone runs
`installers/omp-dup-watch.sh` again. Deleting the state file
(`/mnt/nvme/sami/tmp/omp-dup-watch/state.json`, or `$XDG_STATE_HOME/omp-dup-watch/` without that
disk) forgets every open id, so each one still duplicated is said again.
