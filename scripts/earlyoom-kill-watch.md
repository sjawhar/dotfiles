# earlyoom-kill-watch

Tells a session over Envoy when the host's earlyoom kills one of its processes.

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
   minute are one notice. The notice says what was killed, when, its RSS, the host's available
   memory at that moment and now, why the tool exited 143, and what to do: run fewer jobs in
   parallel, run the work as a Job in the dispatch cluster (`skill://running-heavy-jobs`), or
   retry when available memory is back. A failed send is tried again 30 s later.
4. Every minute it logs one line for any reader that wants the rate:

   ```
   kills_this_hour=17 hour=2026-11-02T15Z kills_prev_hour=0 unmatched_this_hour=15 notices_this_hour=0 tracked=423 pending=0 sample_ms=253
   ```

   `journalctl --user -u earlyoom-kill-watch -g kills_this_hour -n 1 -o cat` reads the last one;
   `earlyoom-kill-watch --counts` prints the saved counts of the last 48 hours as JSON.

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
`$XDG_STATE_HOME/earlyoom-kill-watch/state.json`) gives the journal cursor, the records and the
unsent batches, so no kill is counted or sent twice. With no state file it reads the journal from
the start of the hour for the count, and sends nothing for kills from before it started.

## Use

```
earlyoom-kill-watch --dry-run --state /tmp/ekw/state.json   # the service loop, notices logged, not sent
earlyoom-kill-watch --to <session id>                       # a drill: every notice goes to that session
earlyoom-kill-watch --replay --since 2026-11-02T15:00Z [--until ...] [--to ID] [--send]
earlyoom-kill-watch --counts
```

`--replay` reads a past window and prints whom each kill would reach, with what the watch knows
now (its saved records and the live process tree); it sends only with `--send`. A kill older than
3 minutes is not in the records, so a replay of an old window shows mostly unmatched kills.
A drill copy (`--to`) starts by naming the session the notice was for.

`scripts/tests/test_earlyoom_kill_watch.py` holds the rules, on real journal lines of 2026-11-02
(`scripts/tests/earlyoom-2026-11-02.jsonl`).

## Start, and turn off

```
installers/earlyoom-kill-watch.sh                   # links the unit, enables and starts it
systemctl --user disable --now earlyoom-kill-watch.service
```

The installer leaves a service that is linked but disabled alone.
