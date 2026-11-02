# omp-root-hang-watch

Tells the chief of staff once when the SPEEDCOACH root omp session hangs on a tool call.

## Why it exists

From 2026-11-02 00:08:09Z to 01:59:36Z the root session of the Rocq speed stream
(`01a16a9e-a9b0-761c-97bf-28d7cf2eaac2`) waited on a `bash` call that never wrote a result.
The session's connection was down at the time. Its transcript wrote no line for 111 minutes,
and two of the lane's pull requests waited for it. Nothing told anybody.

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

## What it does

One Envoy message to the chief of staff (`01a13dca-e979-7264-af32-f654ef57a6f5`) through
`scripts/envoy send --source envoy`. The message names the open calls, the last line's time,
the root omp's pid when one is alive, and the transcript path. One message per stall: the
state file (`$XDG_STATE_HOME/omp-root-hang-watch/<session>.json`) records the transcript's
size and mtime at the alert, and the next alert needs a transcript that moved and stalled
again. A failed send records nothing, so the next run tries again.

It never signals, stops or restarts a process.

## Use

```
omp-root-hang-watch --dry-run                       # print what it would send; send nothing
omp-root-hang-watch --session ID --to ID            # watch another root, alert another session
omp-root-hang-watch --stale-minutes 45              # another stall threshold
```

`scripts/tests/test_omp_root_hang_watch.py` holds the rules.

## Arm, and turn off

```
installers/omp-root-hang-watch.sh                   # links the units, enables the timer
systemctl --user disable --now omp-root-hang-watch.timer
```

The installer leaves a timer that is linked but disabled alone.
