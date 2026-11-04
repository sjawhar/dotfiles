# stream-job: one standing job per stream, without a hand-written unit

A stream's standing job runs a script in that stream's own scratch directory. No unit in
`~/.dotfiles` may hold an absolute path, so until now each lead hand-wrote a systemd unit into
`~/.config/systemd/user`, where nobody reviews it and a rebuild of the box loses it.

Instead: write one small file. `scripts/stream-job`, started by `devbox/stream-job.timer` every
5 minutes, runs the jobs that are due. `scripts/unit-health-watch` reads the same state and
alerts the box-health lead when a job stops finishing, naming the owner your file gives.

## Write the job file

One file per job, `~/.config/stream-jobs/<name>.job`. The name becomes the state directory, so
keep it to letters, digits, `.`, `_` and `-`.

```ini
# ~/.config/stream-jobs/example-disk-free.job
owner=01a18385-f8c8-74a9-b6ff-694fb5fe87a7
command=df -B1 --output=avail /mnt/nvme
workdir=/mnt/nvme/sami/tmp
every=15m
timeout=2m
persistent=yes
```

| Key | Needed | What it means |
| --- | --- | --- |
| `owner` | no, but write one | The session id or Envoy role to go to when the job stops finishing. It is what the alert names. Without it the alert says "nobody named". |
| `command` | yes | One shell command line, run with `/bin/sh -c` under `nice -n 15`. Quote it as you would in a shell; do not wrap the whole line in quotes. |
| `workdir` | no | Where to run it. Default `$HOME`. A directory that does not exist is recorded as `no-workdir` and the job does not run. |
| `every` | yes | How often: `90s`, `15m`, `1h`, `2d`, or a bare number of seconds. Measured from the last START, so a job that takes two minutes still runs on its own period. |
| `timeout` | no | How long one run may take before it is killed. Same spans as `every`. Default 10 minutes. |
| `persistent` | no | `yes` means a run missed while the box was down fires once when the box returns. Default no: a job more than one whole period late is run once to move its clock on, and the missed runs are not made up. |

Lines starting with `#` are comments. An unreadable file, a file with no `command` or no
`every`, and a line that is not `key=value` are each skipped with a line in the runner's
journal (`journalctl --user -u stream-job.service`); nothing is guessed at.

## A worked example

A 15-minute job that records how much room the big disk has left, in a stream's own scratch
directory:

```bash
mkdir -p /mnt/nvme/sami/tmp/example-stream
cat > ~/.config/stream-jobs/example-disk-free.job <<'EOF'
owner=01a18385-f8c8-74a9-b6ff-694fb5fe87a7
command=df -B1 --output=avail /mnt/nvme
workdir=/mnt/nvme/sami/tmp/example-stream
every=15m
EOF
stream-job --dry-run        # says what is due, runs nothing
stream-job                  # runs it now
```

Nothing else arms it: the runner picks the file up on its next pass.

## What the runner records

Under `~/.local/state/stream-jobs/<name>/`, one fact per file:

| File | What it holds |
| --- | --- |
| `last-start` | `<epoch seconds> <UTC stamp>` of the run that started last |
| `last-finish` | the same, for the run that ended last; written after everything else, so a reader that sees a finish knows the rest is already there |
| `last-exit` | the exit code, or `timeout after 120s`, `no-workdir`, `did-not-start` |
| `last-line` | the last line the run printed, at most 500 characters |
| `last-output` | the run's whole output, last 64 KiB |
| `lock` | held while a run is going, so two copies of a job never run together |

Read a job with `cat ~/.local/state/stream-jobs/<name>/last-*`, and every job at once with
`unit-health-watch --report`, whose second table is the jobs.

## When a job stops finishing

`unit-health-watch` says `STREAM JOB NOT FINISHING` once, to the box-health lead, when a job
has not finished within three times its own `every`. The message names the job, its owner, when
it last started and finished, its last exit and its last line. It clears by itself on the first
run that finishes.

The runner itself alerts nobody. One voice for a quiet job is enough, and it belongs to the
watch that already speaks for every other quiet unit on this box.

## Adding the timer to a new box

`installers/stream-job.sh` links and enables the timer, and `devbox/expected-timers.txt` holds
`stream-job.timer`, so `installers/expected-timers.sh` arms it with the rest and the watch says
so if it ever goes missing.
