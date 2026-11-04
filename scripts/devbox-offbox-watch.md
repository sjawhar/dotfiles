# devbox-offbox-watch

A watch of sami-devbox that runs on **sami-rocqperf**, never on sami-devbox. Armed by
`installers/devbox-offbox-watch.sh`, which refuses to arm on the machine it watches.

Three files, all in `scripts/`:

| File | Where it runs |
| --- | --- |
| `devbox-offbox-watch` | sami-rocqperf. Decides everything; sends every message. |
| `devbox-offbox-probe` | sami-devbox, shipped over ssh stdin. Read-only: the stall signals, the merge train's state, the registry. |
| `devbox-offbox-act` | sami-devbox, shipped over ssh stdin. `live`, `restart`, `await`. Kills nothing. |

The two remote halves travel over stdin instead of being called from the box's `~/.dotfiles`, so
the watch can never run against a copy of itself that is a publish behind.

## Why it exists

On 2026-11-04 sami-devbox stalled from about 16:40Z and needed a cloud reset at 18:05:53Z. Every
session on it, every loop inside a session, and every watcher armed on it died at the same
moment, and nothing off the box noticed. Sami called the box a single point of failure
(dispatch://INFRA-993/ask/d13b86a5). A standing job that watches a machine has to run on another
machine; that is the only thing this script does differently from its siblings in `omp/`.

## The registry

`/mnt/durable/sami/restart-registry/sessions/<session-id>.entry` on sami-devbox, plain
`key=value` lines. Its own `README.md` has the format, a worked example, and the rule that each
owner writes and updates its own file. The watch reads it and never writes it.

## What it does each run

1. One ssh probe. Two failures in a row alert the SRE and the chief of staff.
2. The writeback-stall test: a process in state D running `umount` or `fusermount3`, or
   `/proc/pressure/io` full avg10 above 5. A stall that holds for 10 minutes is its own alert, and
   while one holds **no session is restarted** — a resume into a stalled box hangs, as 87 did on
   2026-11-04. `--remote-proc` points the test at a private procfs, which is how it is tested
   without stalling anything.
3. The merge train, `/mnt/nvme/sami/tmp/verified-linux/redesign/merge-train`: its operator (the
   `agent` in `runner.lock`) and the Verified Linux lead hear when the queue holds work and
   `events.tsv` has not moved for 30 minutes, or when `runner.lock` names a pid that is not
   running. The train's own README states no stall threshold of its own — only the "Taking over"
   procedure, which the alert quotes — so those two conditions are this watch's brief, and each
   alert says so.
4. Liveness, for every entry with `auto_restart=yes`: `omp-session-live <id>` run on sami-devbox,
   **and nothing else**. Never an `lsof` on a transcript path: on 11-03/04 that reported a working
   lead dead and "restored" it four times while it worked.
5. A dead one is restarted in a new tmux window of its `tmux` session, named for its `title`, from
   its `cwd`; when omp-session-live says it is live, its `after` message is sent.

## The four refusals

* **A writeback stall.** Nothing is restarted while one holds.
* **3 restarts in 10 minutes, 10 in an hour**, across every session. Past that it reports and
  stops, and starts again on its own once the windows empty.
* **A live process naming the session**, even one `omp-session-live` does not count (any process
  whose command line holds both `omp` and the id). It can therefore never make a duplicate, and it
  never restarts or moves a live box.
* **A session that died again within 30 minutes of its restart.** Reported once, then blocked
  until omp-session-live sees it live.

Every one of them is an Envoy message, deduped to one per condition, to the SRE and to the holder
of the session's role. Every restart names the full `--dir` list its resume gave the box: a box
with too few of them looks healthy and fails hours later at one path (CURL, 2026-11-04).

## When Envoy will not take a message

Envoy's hub is `envoy-hub.tail12ec1c.ts.net`, a tailnet node of its own, so an alert about a dead
sami-devbox still goes out; the installer prints which host the hub is and warns if it ever
becomes sami-devbox. A message Envoy refuses is written to
`~/.local/state/devbox-offbox-watch/undelivered/<time>-<kind>.txt` on sami-rocqperf, and the
journal says so.

## Report-only

It restarts nothing while `DEVBOX_OFFBOX_REPORT_ONLY` is set (the drop-in
`~/.config/systemd/user/devbox-offbox-watch.service.d/report-only.conf` on sami-rocqperf) or
`--report-only` is given. Every run then logs what it would have restarted and sends the SRE one
message naming each session with its tmux, cwd, `--dir` list and resume line. Alerts and refusals
go out as usual. That is how it runs until a person says automatic restarting is live; removing
the drop-in and `systemctl --user daemon-reload` is the switch.

## Why a run is short

`Type=oneshot` with a timer, the shape of every other watch in `omp/`. systemd starts no second
run while one is active, so a run that outlives the 5-minute period makes the timer look stopped:
at 19:46:31Z on 2026-11-04 one run waited 180 s for each of three restarts, stayed `activating`
past three periods, and `unit-health-watch` reported "TIMER STOPPED FIRING". So a run is bounded
instead — one ssh probe, one liveness call (about 0.75 s per `auto_restart=yes` entry), and at
most 3 restarts that wait 45 s each. A session slower than 45 s is not abandoned: the next run
sends its `after` message as soon as `omp-session-live` sees it. `TimeoutStartSec=240` kills a run
that goes long, so systemd is free before the next start.

## Running it by hand

```bash
devbox-offbox-watch --dry-run          # everything it would do; starts nothing, sends nothing
devbox-offbox-watch --report-only      # restart nothing, report what it would restart
journalctl --user -u devbox-offbox-watch -n 100
```

A dry run takes about half a minute: one `omp-session-live` per `auto_restart=yes` entry, about
0.75 s each on sami-devbox.

It never kills, signals or stops any process.
