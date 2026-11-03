# earlyoom-kill-watch

Tells a session over Envoy when the host's earlyoom kills one of its processes, and the
box-health owner when earlyoom kills more than 100 processes in an hour.

## Why it exists

On 2026-11-02 the host's earlyoom on sami-rocqperf (`earlyoom -m 10,5 -s 100,100 -r 60
--sort-by-rss --ignore <omp, tmux, sshd, ...>`, from agent-infra's guard) sent SIGTERM 51 times by
15:23Z, 15 of them in the 15:00Z hour. Each time the host's available memory had fallen to 10%.
The kills are by design: they keep the omp sessions alive. But a killed tool exits 143 with no
message, so its session cannot tell why. RULING (`01a199e0`) spent time on that at 15:21Z, after
its box's `simsci` was killed at 15:03:43Z, 15:04:30Z, 15:10:59Z, 15:11:50Z and 15:20:57Z.

## What it does

One long-running user service, `earlyoom-kill-watch.service` (`omp/earlyoom-kill-watch.service`).
It is a service, not a timer like the other watches, because the pid of a killed process is gone
when earlyoom logs the kill: its owner must be recorded before.

1. Every 2 s it reads `/proc/<pid>/stat` of every process and records each one with at least
   64 MiB resident (today's smallest victim had 190 MiB): its start time, its name, and the
   session that owns it. A record stays 3 minutes after its process ends: the kill line arrives
   within seconds, and processes that big churn (4,268 records in six minutes at a 15-minute
   retention, 3,099 of them python3). One sample takes about 0.3 s at load 370 with about 3,700
   processes; the minute line says `sample_ms`.
2. It follows `journalctl -u earlyoom -o json`. For each `sending SIGTERM to process <pid> uid
   <uid> "<comm>": oom_score <n>, VmRSS <n> MiB, cmdline "..."` line it finds the owner, keeps
   the `mem avail` line just before it, and adds the kill to that session's batch.
3. A batch is sent one minute after its first kill, so several kills of one session in one
   minute are one notice. The notice is at most 600 characters: what was killed (time, pid,
   name, RSS; as many kills as fit, then how many more), why the tool exited 143, what to do (run
   fewer jobs in parallel, run the work as a Job in the dispatch cluster,
   `skill://running-heavy-jobs`), and `Full report: <path>` last. The report has every kill with
   the host's available memory at that moment and its full command line, and how the owner was
   found. A failed send is tried again 30 s later, naming the same report.
4. **The box-level rate.** Every kill counts, matched or not. When more than 100 fall in a
   rolling hour, one message goes to the box-health owner: the Envoy role
   `notifications.role.lead-box-health`, and the SRE session
   (`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) only when nobody holds the role, as
   `load-swap-watch`, `disk-fill-watch` and `omp-memory-watch` do. It is said again only when,
   inside an hour after that message, twice as many kills come as it told ("rate doubled"), or
   after the hour's count falls back to 100 and crosses again. Falling back sends nothing. The
   message says how many kills, how many sessions were told, how many had no session, the most
   killed commands and the memory available now; its report lists kills by command and by
   session, and the counts per clock hour.
5. Every minute it logs one line for any reader that wants the rate:

   ```
   kills_this_hour=17 hour=2026-11-02T15Z kills_prev_hour=0 unmatched_this_hour=15 notices_this_hour=0 tracked=423 pending=0 sample_ms=253 box_rate_1h=17
   ```

   `journalctl --user -u earlyoom-kill-watch -g kills_this_hour -n 1 -o cat` reads the last one;
   `earlyoom-kill-watch --counts` prints the saved counts of the last 48 hours as JSON.

Reports go to `/mnt/durable/<user>/tmp/earlyoom-kill-watch/reports/<UTC>-<kind>.txt` (`--reports`),
not beside the state file: an agent box binds `/mnt/durable/<user>` and not `/mnt/nvme/<user>/tmp`,
and 23 of the 197 notices of 2026-11-02 15:44Z-02:00Z went to sessions in boxes. A machine with no
writable `/mnt/durable/<user>/tmp` keeps them beside the state file. Reports older than 7 days are
removed when the next one is written.

## Volume

Per-session notices are unchanged in number: 197 in the 10 h to 2026-11-03T02:00Z (about 470 a
day), 976-1,913 characters each before, at most 600 now. The box-level message is new: 846 kills
in 48 h, 730 of them in the 00Z hour of 2026-11-03, sent nothing box-level; replayed under the rule
above they are 2 messages (00:40:35Z at 103 kills in the hour; 00:44:49Z when 214 more had come).
What it gives up: no message says a storm is over (the minute line and `--counts` do), and a
storm that grows by less than double after its message is not told again.

It kills nothing and changes no limit. earlyoom's own `-N` hook runs after the kill, when the pid
is gone, and earlyoom's configuration is the guard's, so the watch reads the journal instead.

## How a kill is matched to a session, and how good that is

Best first; the notice names the rule that matched.

| Rule | What it reads |
| --- | --- |
| process tree | the nearest `omp` ancestor of the process (omp's `__omp_worker*` helpers skipped). The host sees a box's processes with shifted uids (166536), and a box tool's ancestors run through the box's omp, so boxes need nothing else. |
| same agent box / same systemd scope | a process reparented away from its omp (a daemonized tool): the one session omp in the same docker scope, or in the same `run-*.scope` that `shims/omp` starts. |
| session-tmp path | a process never sampled whose command line holds `session-tmp/<pid>-XXXXXXXX`: `scripts/agent-session` makes that directory as `$$-XXXXXXXX` and then execs omp, so `<pid>` is the omp. |
| systemd scope description | a process left in a `run-*.scope` that no omp runs in now (its session moved into a box): systemd describes the scope by its command, `.../agent-session omp --resume <id>`. |

The session id of an omp is the transcript it holds open (`.../<time>Z_<id>.jsonl`, the rule
`scripts/agentbox` uses), else its own `--resume`/`--restore` argument, else, for a box omp the
host cannot read, `docker exec <box>` reading the box omp's open files (only when a notice needs it).

Measured on sami-rocqperf at 15:45Z on 2026-11-02: of 247 processes at or above 64 MiB (omp
itself left out), 226 (92%) had an owner: 189 by process tree, 37 by scope. The 21 without one
are processes no session runs under: system services (dockerd, containerd, tailscaled), user
services a session started with `systemd-run` (`resolve-f1-*.service`, whose environment names
no session), and containers started outside an agent box (a `clickhouse-server` test
container). A kill of one of those is **unmatched**: it is counted (`unmatched_this_hour`) and
logged with its command line, and nobody is told, because a guessed recipient is worse than
none. A process that starts and is killed within one 2 s sample, with no session-tmp path in its
command line, is unmatched too.

After a restart the state file (`/mnt/nvme/sami/tmp/earlyoom-kill-watch/state.json`, else
`$XDG_STATE_HOME/earlyoom-kill-watch/state.json`) gives the journal cursor, the records, the
unsent batches and the last two hours of kills with the box-level storm's state, so no kill is
counted or sent twice and an open storm is not news again. With no state file it reads the journal
from the start of the hour for the count, and sends nothing for kills from before it started (a
storm already over 100 then is counted, and told only if it doubles).

## Use

```
earlyoom-kill-watch --dry-run --state /tmp/ekw/state.json --reports /tmp/ekw/reports   # the service loop, nothing sent
earlyoom-kill-watch --to <session id>                       # a drill: every message goes to that session
earlyoom-kill-watch --replay --since 2026-11-02T15:00Z [--until ...] [--to ID] [--send]
earlyoom-kill-watch --counts
```

`--replay` reads a past window and prints whom each kill would reach, with what the watch knows
now (its saved records and the live process tree), and the last box-level message of the window;
it sends only with `--send`. A kill older than 3 minutes is not in the records, so a replay of an
old window shows mostly unmatched kills. A drill copy (`--to`) starts by naming the session the
notice was for; the box-level message goes to the drill session too.

`scripts/tests/test_earlyoom_kill_watch.py` holds the rules, on real journal lines of 2026-11-02
(`scripts/tests/earlyoom-2026-11-02.jsonl`).

## Start, and turn off

```
installers/earlyoom-kill-watch.sh                   # links the unit, enables and starts it
systemctl --user disable --now earlyoom-kill-watch.service
```

The installer leaves a service that is linked but disabled alone.
