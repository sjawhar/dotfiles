# omp-root-hang-watch

Tells the box-health owner once when the Rocq speed lead's root omp session hangs on a tool
call.

## Why it exists

From 2026-11-02 00:08:09Z to 01:59:36Z the root session of the Rocq speed stream
(SPEEDCOACH, then `01a16a9e-a9b0-761c-97bf-28d7cf2eaac2`) waited on a `bash` call that never
wrote a result. The session's connection was down at the time. Its transcript wrote no line for
111 minutes, and two of the lane's pull requests waited for it. Nothing told anybody.

## Whose session, and who hears it

Envoy roles, not session ids, so the watch follows the lead and the owner when their sessions
are replaced:

- **Watched**: the holder of the Envoy role `lead-rocq-speed` (`--role`), read from
  `GET /v1/sessions`, which only reads. Never `GET /v1/roles/<role>`: answering it releases a
  lapsed holder's claim (`scripts/envoy-role-holder-watch`). A hung root stops heartbeating and
  Envoy then drops it from the list, so the last holder the watch resolved stays in the state file
  and is watched while Envoy lists no holder, or does not answer. `--session ID` watches one
  session instead.
- **Told**: `notifications.role.lead-box-health` (`--to`), the box-health owner (the SRE
  session holds it). Before 2026-11-03 it was the chief of staff's session id; that session holds
  no Envoy role. Nobody holds the role: the send fails, the run exits 1, and the next run tries
  again.

## What it checks

Every 5 minutes it reads the session's transcript,
`~/.omp/agent/sessions/<dir>/<time>_<session>.jsonl`, and alerts when both are true:

1. The transcript has had no new line for 30 minutes (its mtime).
2. The session has a live child job: in the last 4 MiB of the transcript, a
   `tool_execution_start` written after the last assistant message, with no `toolResult` for
   the same `toolCallId`.

A session that is idle at its prompt has no open call, so it is quiet however long it sits.

`wait` and `task` block on subagents and background jobs by design. In the session's own
history, 62 calls took 20-39 minutes (`wait` up to its 30-minute cap). When every open call is
one of those two, it alerts only when no file under the session's directory (subagent
transcripts, job logs) changed in the window either.

The script's docstring says why it does not use the process tree or subagent transcripts as
the signal.

## What it sends

One Envoy message per stall, at most 600 characters: the session and its role, how long it has
been still, the open calls (with their intents while they fit), the root omp's pid when one is
alive, and `Full report: <path>` last. The report
(`$XDG_STATE_HOME/omp-root-hang-watch/reports/<UTC>-hang.txt`, pruned after 7 days) lists every
open call, the transcript and the commands to look further.

The state file (`$XDG_STATE_HOME/omp-root-hang-watch/role-lead-rocq-speed.json`) holds the
holder, its transcript's path and, after an alert, the transcript's size and mtime: the next
alert needs a transcript that moved and stalled again. A failed send records nothing, so the next
run tries again. A new holder starts a new record.

Expected volume: 0 messages in the 48 h to 2026-11-03 (104 quiet runs); one per real stall.
What it gives up: the chief of staff no longer hears of a hang directly; the box-health owner
does, and decides who else must know.

It never signals, stops or restarts a process.

## Cost and time limit

A quiet run reuses the transcript path from the state file, so it lists none of the 1,655
session directories (0.32 s of the old run), and costs about 0.4 s of CPU: python3 start-up,
one `GET /v1/sessions` (10 s cap), one stat. It lists them only before it judges a stall, to
read the newest transcript. The unit's limit is 240 s: the 4 failed runs of 2026-11-02/03 were
kills at the old 120 s during box-wide stalls in which four other watches timed out too.

## Use

```
omp-root-hang-watch --dry-run                       # print what it would send; send nothing, write nothing
omp-root-hang-watch --role lead-x --to ID           # watch another role's holder, alert another session
omp-root-hang-watch --session ID                    # watch one session id
omp-root-hang-watch --stale-minutes 45              # another stall threshold
```

`scripts/tests/test_omp_root_hang_watch.py` holds the rules.

## Arm, and turn off

```
installers/omp-root-hang-watch.sh                   # links the units, enables the timer
systemctl --user disable --now omp-root-hang-watch.timer
```

The installer leaves a timer that is linked but disabled alone.
