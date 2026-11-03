# omp-memory-watch

Every 30 s, checks that no agent session on **this** machine is heading for the memory ceiling
its `omp` process runs under, and alerts the session itself over Envoy while it can still
checkpoint and shed work on its own terms. In the same run it biases the kernel's OOM choice
away from each session's own omp ([below](#action-one-the-oom-kill-takes-the-runaway-child)), and
resumes a tmux pane whose omp died without ending its session
([below](#action-two-resume-a-pane-whose-omp-died)).

## Why it exists

On 2026-10-27T05:55:14Z the kernel OOM-killed the Verified Linux owner session inside its own
systemd scope. The kernel's record (`journalctl -k`):

```
Memory cgroup stats for /user.slice/user-1000.slice/user@1000.service/app.slice/run-u174595.scope:
memory: usage 25165824kB, limit 25165824kB, failcnt 121421
swap: usage 0kB, limit 0kB, failcnt 0
oom-kill:constraint=CONSTRAINT_MEMCG,...,task=omp,pid=3398496,uid=1000
Memory cgroup out of memory: Killed process 3398496 (omp) total-vm:177177208kB,
  anon-rss:15522968kB, file-rss:100032kB, shmem-rss:0kB
oom_reaper: reaped process 3398496 (omp)
```

About 40 in-session subagent lanes died with it and nothing warned first. (dispatch://INFRA-602)

**Prevention belongs to the session, detection to this watch.** A session's own self-watch warns
first and the session sheds its own lanes; this watch sits above the self-watches and kills
nothing and sheds nothing. It acts in the two ways below, which Sami asked for on 2026-11-02 at
01:00Z: kill the runaway subprocess instead of the whole session, and restart the sessions that
did die.

## Action one: the OOM kill takes the runaway child

When a scope reaches its `MemoryMax` the kernel's memcg OOM killer kills the member with the
largest *badness*: its RSS, swap and page tables, plus `oom_score_adj × limit / 1000`. With every
`oom_score_adj` at 0 that is the session's omp, the biggest single process (14.9 of 24 GiB at
the kill above), and every subagent dies with it.

A process may raise an `oom_score_adj` of its own uid but never lower one, so omp cannot be
biased down without root. Every run therefore raises every **other** process in each capped omp
scope to `--oom-bias-adj` (default +500). Under a 24 GiB limit +500 adds 12 GiB to each one's
badness and nothing to the omp's. The live case at 2026-11-02T01:09Z, `run-u20238.scope` at
22.19 of 24 GiB with `oom_kill 1` already: a 3.21 GiB `tsgo` scores 15.2 GiB against the
session's 1.71 GiB omp, so the compile dies and the session lives.

The rule protects **the session's own omp**, not every process named `omp`: the one omp in the
scope whose parent is outside the scope — the process `shims/omp` starts through `systemd-run
--scope`. A nested omp (a subagent, a js-eval helper) has its parent inside the scope and is
biased like any other child: a runaway subagent holding 10 GiB is a far better victim than the
session and its other lanes. A scope where no omp has its parent outside is counted
(`scope(s) with no session omp found`) and keeps every omp at its old value.

- Only scopes with a `memory.max` of their own. An uncapped session shares the login scope with
  every other session in the login, and that scope is not one session's to bias.
- An adj already at or above the target is counted and left: it is never lowered.
- A process this user may not write (another uid, as inside an agent box) is counted as
  `refused`. EPERM is normal there. An agent box's own OOM kill is not helped by this pass.
- The pass is fenced: an error is logged in the run's line and the alerting goes on.
- `--dry-run` counts what it would raise and writes nothing. `--no-oom-bias` turns it off.

The counts go in the run's log line, for example
`oom bias +500: 55 capped scope(s), raised 486, 594 already at or above, 0 refused (EPERM), 5 gone`.

## Action two: resume a pane whose omp died

Each run reads every pane of the default tmux server (`tmux list-panes -a`) and the processes
below each pane, and finds the session the way `tmux-resurrect-omp` does: the session file an omp
holds open, else the `--resume` id on its command line; for a pane running `agentbox new <spec>
-- omp …`, the launcher's own line, else (once per pane per 5 min, one box per run, since each
`docker exec` costs about 1.5 s) the omp inside that one box. The state file keeps, per pane, the
session, the working directory, the window (`#{window_id}`, since `renumber-windows` is on and a
window's index changes) and for a box the launcher's spec.

The pass is bounded, because at load 425 on 2026-11-02 an unbounded one took 33 s and the unit
killed the next period's run at its 25 s limit. Every process is read once, from its
`/proc/<pid>/stat` (parent, name, start time); the walk stops at an omp, so a session's tools are
never read; an omp's open files are scanned only when its session was last read more than 5 min
ago, at most 10 scans a run; the box lookup runs only with 11 s to spare; and the pass stops 14 s
after the run started (the oom bias at 6 s), the next run starting at the first pane or scope it
did not reach. Measured at nice 15 and load 428: 1.6-2.3 s a whole run.

A recorded pane that is now **idle** — its shell has no child process, or tmux marks it dead — is
resumed as the lead does it by hand, with no prompt after the id:

```sh
tmux respawn-pane -k -t <pane> -c <cwd> bash -i -c '( setsid ~/.dotfiles/scripts/agentbox move --wake <id> <notice> & ); cd <cwd> && ~/.dotfiles/shims/omp --resume <id>; exec bash -i'
tmux respawn-pane -k -t <pane> -c <cwd> bash -i -c '( setsid ~/.dotfiles/scripts/agentbox move --wake <id> <notice> & ); cd <cwd> && ~/.dotfiles/scripts/agentbox new <spec> -- omp --resume <id>; exec bash -i'
```

with `~` expanded: the shim by its absolute path, because a pane that starts omp from `PATH` can
miss `PI_CONFIG_FILES`, which broke three sessions on 2026-11-01. The resume used to end in `.`,
which omp records as a user message and the transcript store therefore counted as text Sami typed;
`agentbox move --wake` wakes the session instead with one Envoy notice once the resumed process has
registered (log: `~/.cache/omp/agentbox/wake-<id>.log`). Right before the respawn the watch asks
tmux for the pane again: `agentbox move` hangs a session up and respawns its pane into the box
launcher seconds later, and on 2026-11-02 at 05:38:05Z a run that decided in that gap read the old
pane pid and killed the launcher of session `01a14b36` with its own respawn. It is **left**, with
the reason in the journal, when:

| Reason | Why |
|---|---|
| the pane was respawned while the run decided (its pid is not the listed one) | something else, such as `agentbox move`, has already started the pane again |
| `agentbox move` owns the session's respawn (`wait:`; the record is kept and read again next run) | the move respawns the pane itself, 1 to 17 s after its hang-up; a resume in that gap gives one transcript two writers. See below |
| the pane still runs omp or an agentbox launcher | it is not dead |
| the session's transcript ends in a `session_exit` with `kind: "normal"` | it ended on purpose; the test is `normal_exit_at` from `envoy-role-holder-watch`, imported, not copied |
| another live omp holds the session | resuming it twice would give one transcript two writers |
| it was resumed less than `--restart-cooldown` (1 h) ago | at most once per session per hour |
| it was last seen running more than 1 h ago | it died while nothing watched |
| no session was read while it ran, no transcript is found, or its directory is gone | nothing safe to resume |
| the pane has run something other than omp for 2 min | it is no longer an omp pane; the record is dropped. A shorter spell is the shim's bash finishing or `exec bash -i` reading `.bashrc`, and is waited out |

A `session_exit` of kind `signal` (a `kill <pid>`, SIGTERM) is **not** a normal end, so a session
stopped that way is resumed, once. A memcg OOM kill is SIGKILL and writes no `session_exit` at all.
A pane this watch never saw running omp is never touched, so nothing that died before the
restarter was deployed is resumed by it. A pane that is gone (its owner closed it) is forgotten,
not resumed, unless a move holds its session.

### A session in an `agentbox move`

A move hangs the session's omp up with SIGHUP, so its transcript ends in a `signal` exit like a
machine loss, a lead's restart or a memory stop, which are about 55 of the 73 signal exits read in
the study for dispatch://INFRA-898 and must all be resumed. The exit entry cannot tell them apart,
so the move's own log decides: `~/.cache/omp/agentbox/move-<id>.log`, read by `who_respawns`,
the one place that decides who brings a session back (the `intent` omp is to record in
`session_exit.data` goes there). Only the session's latest move counts, and only its lines from
the last time this watch saw the session running, so a move the session already came back from
is history.

- **The move is running**: its latest step (the start, `hung up; waiting for its teardown`,
  `exited after`, `respawn of pane … failed (try N of 40)`, `respawned into`) is less than 10 min
  old. The pane is left with `wait: agentbox move owns its respawn (<time>: <step>)`, and a pane
  that is gone is kept (`kept: the pane is gone, and agentbox move owns its respawn`), not
  forgotten. A move that logs nothing for 10 min has died, and the pane is decided as any other.
- **The move gave up**: its last line says `the session is DOWN` (every respawn try failed),
  `pane … is gone; nothing respawned`, or, before 2026-11-02, tmux's `respawn pane failed`. The
  session is resumed as the box the move was making (`agentbox new <the move's spec> -- omp
  --resume <id>`), after the other checks above. A pane still there is respawned in place. A pane
  that is gone gets a **new pane in the window it was in**, opened as `scripts/agent-new` opens one
  (`tmux split-window -d -t <window id> -c <cwd>`, and once more after `select-layout tiled` when
  tmux has no space). A window still full after that, which is common here (the agents windows
  hold 10 and more panes; a drill window refused its third split on 2026-11-02 at 15:29:49Z), or
  a window that is gone, gives way to a new window `omp-<first 8 of the id>` in the same tmux
  session (`tmux new-window -d -t <session>: -n omp-<id>`). Either way the box-health owner is
  told which move gave up and where the session now runs.
- **A move to another machine is running** (`agentbox move --to <machine>`): its relauncher keeps
  `~/.cache/omp/agentbox/move-to/active/<id>` (`pid=`, `target=`, `start=`) until the move ends.
  While that file is there and its pid is alive, the pane is left with `wait: agentbox move --to
  <machine> owns it`, and a gone pane is kept. On 2026-11-03 at 04:10:57Z this watch resumed
  01a19e3c on sami-rocqperf 6 s after its move to sami-devbox hung it up, because it read only the
  same-machine move log.
- **The session moved to another machine**: the marker `agentbox-moved` in its transcript
  directory, or `~/.omp/agentbox-moved/<id>`, names this machine as `from=`. It runs on the target
  now and one session must not run on two machines, so it is left (`left: it moved to <machine> …`)
  and the record dropped. As in `scripts/agent-session`, a marker whose `from=` is another machine
  is inert here.

Why a new pane and not an alert only: R3A (`01a16c67`) closed its pane %74 (`agents3:7.4` then)
with its omp on 2026-11-02 at 10:20:48Z, the move then tried 40 times and logged DOWN at
10:32:00Z, and nothing but that log knew: the session stayed down until 14:31:16Z, when it was
resumed by hand on the host in a new pane (%1641, a new window `r3a`). The restarter holds what
that hand resume needed (the session, the directory, the move's box spec, the window), and an
alert would only have handed the same command to a person four hours later. The pane goes into
the window it was in (dispatch://INFRA-202 gives each stream one window), never one this watch
guesses. When no new pane can be opened (the tmux session is gone too), and whenever a respawn
this watch decided on fails, the box-health owner gets the exact command to run instead.

`--restart-dry-run` (and `--dry-run`) decides and logs `DRY RUN, would resume: … <command>` (for a
gone pane, `DRY RUN, would resume in a new pane of window <id>`), tells nobody, and
respawns nothing. `--no-restart` turns it off. On a machine without `~/.dotfiles/shims/omp` or
without a reachable tmux server the pass does nothing and says so in the run's line. The unit
sets `TMUX_TMPDIR` and puts `shims/` first on `PATH`, as `envoy-role-holder-watch.service` does:
the socket lives where `.bashrc` puts it, and `/usr/bin/tmux` (3.4) cannot talk to the 3.8 server.

## Action three: a session with no scope of its own gets one

`shims/omp` starts each session in a `run-*.scope` with a ceiling and `OOMPolicy=continue`. When
both of its scope probes time out (two 25 s user-manager timeouts 10 s apart, at load 450) it
starts the session in its shell's cgroup instead: the login scope `session-c5.scope`, or tmux's.
01a19679 started that way at 11:57Z on 2026-11-02 and ran uncapped until it was moved by hand at
13:36-13:45Z. Sessions started before shims/omp c31519b run in a scope with systemd's default
`OOMPolicy=stop`, which ends the whole session when the kernel kills one runaway child
(2026-11-02T03:22:08Z, a session and 40 subagents).

Each run takes every top-level `omp` of this user on the host (its parent is not an omp, and it
has run for at least a minute) whose cgroup is a `session-*.scope` or `tmux-spawn-*.scope`, or a
`run-*.scope` whose `OOMPolicy` is `stop`, and moves it into a new delegated
`run-c<first 16 hex of the session id>.scope` in `app.slice` with `OOMPolicy=continue`:

- From a login or tmux scope it moves **the session's own process tree only** (the omp and its
  descendants still in that cgroup), never the rest of the login scope. The ceiling is the one
  the session asked for (`OMP_MEMORY_SCOPE`, which the shim exports before it falls back), else
  the shim's 24 GiB default, with `MemorySwapMax=0`. A tree whose RSS is already at or above
  that ceiling is left, and the run says so.
- From an `OOMPolicy=stop` scope it moves every process in the scope and keeps the scope's
  `MemoryMax`, `MemorySwapMax` and `TasksMax`.

The scope is made with `StartTransientUnit` holding the omp, then each other process is added
with `AttachProcessesToUnit`, one pid a call (a pid that has exited fails the whole call). Each
move is one journal line (`scope adopt: moved omp <pid> (<session>, N process(es)) from <old>
into <new> …`). An omp that exits before its scope is made is left with no line and no retry;
a create refused for an omp still running logs `scope adopt: FAILED to create …`. No process is
signalled, and memory a process already holds stays charged to the old cgroup: cgroup v2 moves
processes, not pages. A process left over (out of time, or born in the old cgroup since) is
attached on a later run. Each scope's `OOMPolicy` is read once, with one `systemctl show` for
every scope not yet known, since a scope's policy and name never change. The
pass stops 40 s after the run starts, and begins a systemd call only with 10 s left.
`--no-adopt-scopes` turns it off; `--dry-run` logs `scope adopt: would move …` and moves nothing.

## Action four: each worker broker, and each lane's services, get scopes of their own

omp runs one worker broker per project directory (`omp __omp_worker_daemon_broker`, which renames
itself `omp daemon brok`). The first session that needs one starts it as a plain detached child
(`packages/coding-agent/src/launch/client.ts`, `#spawnBroker`), and a child keeps its parent's
cgroup. So the broker, and every background service any session of that project starts through
it (the bash tool's services, listed under `proc://`), ran in that first session's scope. On
2026-11-03 the broker of one project checkout under `~/src` (pid 77103) held the services of 14
sessions in the chief's `run-c01a13dcae9797264.scope`: they counted against the chief's 24 GiB,
earlyoom's kill notices for them went to the chief, and 42 brokers on the box were each in some
session's scope. Action three made it worse for 77103: it moved the chief's whole
`OOMPolicy=stop` scope, broker included.

A scope outlives its session: it stays while any process is in it, whether or not the process
that made it is alive, and nothing in it is signalled when that process exits. Stopping the
scope (`systemctl --user stop`, or `OOMPolicy=stop` after a kernel OOM kill in it) kills every
process in it. Measured on sami-rocqperf on 2026-11-03 with a throwaway scope whose leader exited
at once: its `setsid` child ran on and the scope stayed `active`; `systemctl --user stop` then
killed the child.

Each run finds every broker of this user on the host whose runtime dir is under `~/.omp` (a
grading jail's broker has its own HOME, and its jail scope is its limit, so it stays; a broker in
an agent box is the box's) and:

- moves the broker into `omp-broker-<first 16 hex of sha256(its runtime dir)>.scope`, described
  `omp worker broker for <project dir>`, with the ceiling the broker inherited
  (`OMP_MEMORY_SCOPE`, else 24 GiB), `MemorySwapMax=0` and `OOMPolicy=continue`;
- gives each direct child of the broker an owner: its `OMP_SESSION_ID` when that is not the
  broker's own (a service runs with its owner's environment), else the `owner` of the live record
  in `<runtime dir>/daemons/*/meta.json` whose `pid` it is (newest first; reading 1,020 records
  took 8.6 s at load 400, so only these are read, once per process), else nobody once it has run a
  minute (the shared headless browser, which carries the broker's id);
- moves each owned child, with its descendants, into `run-c<first 16 hex of the owner>-svc.scope`,
  one per owner, described exactly `omp session <owner id>`, with 24 GiB, `MemorySwapMax=0` and
  `OOMPolicy=continue`. A child that serves nobody goes with the broker.

Both are made with `Delegate=true`: systemd (255 here) adds a process to a running scope only when
the scope is delegated, and refuses the rest with `Process migration not available on
non-delegated units`. `Delegate` cannot be set on a running scope. The first live version of this
pass (2026-11-03, 22:16-22:21Z) made 62 scopes without it and then failed 75-125 attaches a run,
so it was taken out. A scope of the name that is not delegated keeps what it holds, and new
processes go to the next `<name>-2.scope`, `-3` and so on, with the same Description.

These are the names `earlyoom-kill-watch` reads: a kill in a `-svc` scope goes to the session its
Description names, and one in a broker scope to the box-health owner. Only processes still in a
cgroup the broker's tree inherited are moved (the scope the broker was in, or has been in since
this watch first saw it); one a service put in a scope of its own is left. No process is
signalled, and memory already held stays charged to the old cgroup. Action three never takes an
omp worker for a session. The pass runs after action three and stops 70 s after the run starts;
`--no-adopt-scopes` turns it off with action three, and `--dry-run` logs
`broker scopes: would move …`. omp's own `proc://` lists every service of the project's broker,
whoever owns it: that is omp's listing, not the cgroup, and this pass does not change it.

## What it measures, and why not RSS

`shims/omp` starts each session under `systemd-run --user --scope -p MemoryMax=$OMP_MEMORY_MAX
-p MemorySwapMax=0`, so the **scope** is the session's whole budget — its tool subprocesses
included — and the scope is what the memcg OOM killer compares with the ceiling. The omp
process's RSS is not:

| At the 05:55:14Z kill | |
|---|---|
| The scope's ceiling | 24 GiB (`memory.max` of `run-u174595.scope`; every ancestor is `max`) |
| The scope at the kill | 24 GiB, `failcnt 121421` |
| The `omp` process | 14.9 GiB resident — **62%** of what hit the ceiling |
| 20 `python3` tool subprocesses in the same scope | 13.1 GiB resident between them |

So the signal is the scope's **unreclaimable** memory, `anon + shmem + kernel + sock` from
`memory.stat`. In a scope `shims/omp` starts, swap is off (`MemorySwapMax=0`), so file cache is
the only part of `memory.current` the kernel can give back, and counting it would alert on a
scope in no danger. A scope `ossv-worker` starts sets `MemoryMax` alone, so its
`memory.swap.max` is `max` and its anonymous memory **can** page out: there the unreclaimable
number can stay low while the scope still reaches its ceiling, which is what the wall trigger
below exists for. Every alert reads `memory.swap.max` per scope and says which shape it is; it
used to assert `MemorySwapMax=0` for all of them, which was false for every worker scope.

A session with **no scope of its own** (started outside `shims/omp`, so it lives in the login
session's scope — `session-c45.scope` here, 744 processes and 51 GiB of other sessions' work)
is measured on its own process RSS instead, against the machine's RAM. Charging a shared login
scope to one session would alert on every session in the login.

## The threshold

**70% of the scope's own ceiling**, which is **16.8 GiB of 24 GiB** for a default session, with
**7.2 GiB of headroom**. Measured on this box on 2026-10-27, 159 scopes sampled every 30 s for
13.7 min:

- Of the 12 scopes that reached 5 GiB, the fastest **sustained** rise (best 5-minute window) was
  0.99 GiB/min and the median was 0.48 GiB/min. One-minute bursts reach 3.5 GiB/min but do not
  sustain, which is what the 60 s grace is for.
- Detection costs at most 90 s (a 30 s period plus the 60 s grace) = 1.5 GiB at the fastest
  sustained rise. **5.7 GiB is left: 5.8 minutes at 0.99 GiB/min, 12 minutes at the median.**
- That is the shed budget. Re-spawning 11 lanes took the Verified Linux lane 6 minutes after the
  kill (~33 s a lane), and writing handover files is seconds, so minutes is the right order.
- 75% would leave 4.5 minutes after detection at the fastest sustained rise; 60% (14.4 GiB) is
  inside the range healthy sessions reach — one scope peaked at 13.2 GiB during the sample.
- No healthy scope came near 16.8 GiB in that window (the highest peak was 13.2 GiB), so the
  measured false-positive rate is zero.

**A fixed RSS number does not work on this box.** The live ceilings are 24 GiB (46 sessions),
none at all (15), 4 GiB (5), 12 GiB (2), 48 GiB (1) and 16 GiB (1). A 12 GiB threshold is above
the entire ceiling of the 4 GiB scopes and at the ceiling of the 12 GiB ones — it would warn
after the kill — while on the 48 GiB scope it would fire at 25%.

**It sits above the session self-watches.** The known self-watch
(`/mnt/nvme/sami/tmp/verified-linux/handover/ol-rss-watch.sh <pid> 10`) warns at 10 GiB of omp
RSS. Where the growth is in the omp process — the only thing that self-watch can see — 16.8 GiB
of scope pressure is reached only after RSS passes 10 GiB, so the session's own watch always
speaks first. Where the growth is in the session's **tool subprocesses**, the self-watch is blind
to it and this watch is the only warning, which is why every alert says whether a self-watch was
found and never implies there is none when it cannot tell.

An uncapped session alerts at a flat `--unlimited-warn` (16.8 GiB of its own RSS): the level a
capped session is warned at. A fraction of 251 GiB of RAM is a number no single session reaches.

## The second trigger: a scope that REACHES its wall

The 70%-for-60 s crossing is a *forecast*, and it needs the pressure to be both unreclaimable
and held. A scope can spend its whole budget and meet neither test. Measured on
**ossv-agent-open on 2026-10-30 at 19:37Z**: of 95 worker scopes, **31 had touched their 24 GiB
wall** (`memory.events` `max` above zero, up to 42 422 times for one scope), while the watch
logged `0 close enough to sample` — not one of them was even above the 45% sampling level on
unreclaimable memory. Four had ever been reported.

Two reasons, both real on these VMs:

- **The pressure is reclaimable.** `ossv-worker` starts each worker with `systemd-run --user
  --scope -p MemoryMax=24G` and **no** `MemorySwapMax` (`bin/ossv-worker:577-578`), so
  `memory.swap.max` reads `max`: anonymous memory pages out, page cache fills the scope, and
  `anon+shmem+kernel+sock` stays low while `memory.current` sits on the ceiling.
- **The touch is shorter than the grace.** A scope that reaches the ceiling and falls back
  inside one 30 s period leaves no trace in any level-and-hold test.

So a capped scope is also reported when, since the last poll, either

- the **`max` counter of its `memory.events`** rises — the kernel refused an allocation at
  `memory.max` and had to reclaim to satisfy it — or
- its **`memory.peak`** rises to `--wall-fraction` (default 0.98) of the ceiling.

No hold, no grace, no rate. One report per scope per `--wall-cooldown` (default 15 min); touches
inside the cooldown are counted and the next report says how many it covers. The **message**
(at most 600 characters, see [Who hears about it](#who-hears-about-it)) says the scope, the
session, the peak against the ceiling, how much of the ceiling is reclaimable page cache
(`memory.current` less the unreclaimable part, the one number that decides whether the rest
means anything), the verdict in one sentence, and what to do. Its **report file** names the
scope, the session, the `ossv-worker` name, `memory.peak`, `memory.current`, the unreclaimable
number, the `oom`/`oom_kill` counters, the **`memory.stat` split** (`anon`, `file`, `shmem`,
`kernel`, `sock`, largest first), the **process count and the RSS sum of every process in the
scope**, the **three largest by RSS with their command lines**, the whole verdict and the advice.

### How urgent the report says it is

Reaching `memory.max` is not by itself a near-death state, and saying it is costs real work. On
2026-11-01 one session on sami-rocqperf got three reports telling it a scope at its wall was
"one allocation away from a memcg OOM kill" while that scope's counters read `max` +697, `oom`
**0**, `oom_kill` **0**, with 10.14 GiB of its 24.00 GiB held as page cache. It nearly cut a
shared fleet gate from 2 slots to 1 — doubling the wait for ~27 lanes, one of which had already
waited 1 191 s — to avert a kill the counters said was not coming.

What the kernel documents (`Documentation/admin-guide/cgroup-v2.rst`):

| Counter | The kernel's words |
|---|---|
| `memory.max` | "If a cgroup's memory usage reaches this limit and **can't be reduced**, the OOM killer is invoked in the cgroup." |
| `max` | "The number of times the cgroup's memory usage was about to go over the max boundary. If direct reclaim fails to bring it down, the cgroup goes to OOM state." |
| `oom` | "The number of time the cgroup's memory usage was reached the limit and allocation was about to fail." |
| `oom_kill` | "The number of processes belonging to this cgroup killed by any kind of OOM killer." |
| `file` | "Amount of memory used to cache filesystem data, including tmpfs and shared memory." |

So a rising `max` with `oom` still 0 is the kernel **succeeding**: usage reached the boundary,
direct reclaim brought it down, the allocation went through. `oom` is the counter that rises
when reclaim fails and `oom_kill` the one that rises when something dies. The kill condition is
"can't be reduced", and a scope holding gigabytes of page cache **can** be reduced.

The report's verdict therefore follows the counters, in this order:

| State | Verdict |
|---|---|
| `oom_kill` > 0 | **urgent** — not a forecast: the memcg OOM killer has already run here |
| `oom` > 0 | **urgent** — reclaim has already failed at `memory.max`; the next failure is the kill |
| `memory.stat` unreadable | **urgent**, conservatively — no part of the charge can be shown reclaimable |
| unreclaimable ≥ `--fraction` of the ceiling (0.70) | **urgent** — at or past the level this watch already calls a session near its limit, so reclaim is nearly out of material |
| `anon` ≥ `--wall-urgent-anon` of the peak (0.50) | **urgent** — the shape a collect-mode box sends the box-health owner at once |
| otherwise | **not urgent** — the pressure is page cache the kernel drops on demand; do not kill builds, halve a shared gate, or abandon a measurement on this report |

The urgent side is a **superset** of what `urgent_reason` sends a collect-mode box's
box-health owner at once, so the owner's report and that immediate note can never contradict
each other. The verdict changes the words, never **whether** or **to whom** a report is sent.

**"Write your handover files first" is unconditional.** It costs seconds and it is right in
every state, so it leads the `DO` line whatever the counters say.

### If the owner does shed, where to shed

The three largest processes explained only 10–29% of a scope at its ceiling in the open4 and
open6 digests of 2026-10-30, so "find the process" was the wrong instruction for those scopes.
The report's last line is chosen from the numbers:

| Condition | Advice |
|---|---|
| the named processes hold ≥ 60% of the peak | **one runaway**: find that process and stop or bound it; shedding lanes will not help |
| every process together holds ≥ 70% of the peak | **many processes**: reduce the fan-out — fewer lanes, fewer tool subprocesses at once |
| neither | **not in any process**: name the dominant `memory.stat` field and what it is (page cache, shmem/tmpfs, kernel slab), and point at the file traffic rather than the lane count |

In the third case, when `memory.current` has already fallen under half the peak, the advice
also says the peak was a burst and nothing on the scope now holds that memory.

The worker name and its session come from the scope's own command lines: `ossv-worker` starts
`omp` with `@<root>/.ossv-workers/<name>/message-*.md`, and `<root>/.ossv-workers/<name>/session`
holds the session id — used only when `session_unconfirmed` is absent beside it, because the
worker's own rule is that the file still names the *previous* session until then. Failing that,
the session is resolved from the process as before.

### The session a wall report goes to is verified

A session id taken from an `omp --resume <path>` is the id its session *directory* was created
with, and a resume replaces it; `OMP_SESSION_ID` is inherited and names whoever started the
box. Either one sent to Envoy reaches whoever holds that id somewhere else, not a silent owner:
on `ossv-agent-repo` on 2026-10-30 that stale id was VERIFY, which runs on sami-rocqperf.

So before a wall report is written the claimed id is checked against `GET /v1/sessions`,
filtered to this box's host. One that is not live here is replaced by the live owner — the
worker's `session` file, else the box's one live session — and the report says the swap
happened. When no live owner can be named, nobody is told directly and the lead's digest says
`session unknown`. An Envoy that does not answer leaves the id unverified and the report goes
where it would have gone: a lookup failure must never read as "nobody is live". The lookup
happens only when a touch exists, never on a quiet period.

**The first run after this trigger arrives seeds and reports nothing.** A counter already above
zero dates from an unknown moment in the past, often before the trigger existed, and reporting
31 scopes' history at once would bury their owners. A scope that appears while the state file
already holds wall memory is genuinely new, so its baseline is zero and its first real touch is
reported.

## What one run does

The alerting part: /proc and /sys reads only, no transcript reading in the decision, no model
call, ~0.4 s. The actions above add 1-2 s at load 428 with 220 panes (2026-11-02); each stops
itself at a time from the run's start (oom bias 6 s, panes 14 s, scopes 40 s), inside the unit's
90 s limit, which whole-box stalls at load 450 need (the old 25 s killed runs in which systemd
itself logged nothing):

1. Every `omp` process on this machine, with the cgroup each runs in (`/proc/<pid>/cgroup`).
2. The ceiling it is heading for: the smallest numeric `memory.max` from that cgroup up to the
   root. All `max` means the ceiling is the machine's RAM and the kill would be a global OOM.
3. The pressure: `memory.stat` for a capped scope, `/proc/<pid>/statm` for an uncapped session.
4. A crossing opens at 70% of the ceiling, alerts after the 60 s grace, and clears under 62%
   (hysteresis) or when the process ends.
5. Independently, a capped scope's `memory.events` and `memory.peak` are compared with the last
   poll's: a rise is a wall touch and is reported at once, with no hold.

**Failed runs.** In the 40 h to 2026-11-03T02:40Z systemd killed 194 runs at the start timeout.
191 of them came before 2026-11-02T14:00Z, under the old 25 s `TimeoutStartSec`, which loaded
runs overran even after 37b2af5 bounded the passes at 01:51Z; f7359dd set 90 s. The other 3
(2026-11-02 at 15:33Z, 18:15Z and 18:18Z) came in user-manager stalls, not from a slow step
here: no timer fired from 18:10:36Z to 18:14:46Z, and the manager logged `Starting` only 20 s and
11 s before its 90 s timeout fired, since systemd 255 arms `TimeoutStartSec` from its event
loop's cached wake-up time (`sd_event_now`), not from the fork. Nothing in this script can bound
that. In the last 6 h of that window, the 00:36-00:52Z memory-pressure episode included, 410
runs took at most 4.41 s and none failed.

The session id is read **from the process** — a session id on its command line, else the
`~/.omp/agent/sessions/**/<stamp>_<id>.jsonl` it holds open — and is resolved only when an alert
is about to be sent, which is what keeps a run to a handful of file reads. When it cannot be
established the alert says "session unknown" and goes to the box-health owner alone; it never guesses.

## Who hears about it

Agent sessions only, never a person. Every message is at most 600 characters: its first ~200 say
what, where, how bad and who acts, and its last line is `Full report: <path>`, a file under
`reports/` beside the state file (`/mnt/nvme/sami/tmp/omp-memory-watch/reports/<UTC stamp>-<kind>.txt`)
that holds the whole detail. Each run deletes the reports older than 7 days.

| What happened | The session | The box-health owner |
|---|---|---|
| a crossing (NEAR) | the alert | only when no session can be named |
| still rising `--escalate` (10 min) after the alert | — | a copy, once |
| an OOM kill in the scope while its crossing is open | — | a copy, once |
| the crossing cleared | one line | never |
| a wall touch | one message | only when no live session on this box owns the scope |
| `--wall-burst` (3) touches on the box inside `--wall-burst-window` (15 min) | — | one message, again only when the count doubles; none when the role already heard every touch in it |
| a session this watch could not resume, or resumed after `agentbox move` gave up | — | one message |

The box-health owner is the holder of the `lead-box-health` Envoy role
(`notifications.role.lead-box-health`), and the SRE session
(`01a18385-f8c8-74a9-b6ff-694fb5fe87a7`) when nobody holds it. A burst episode ends when 15 min
pass with no touch, so a count that dips under 3 and comes back is not news.

Why (dispatch://INFRA-672; sami-rocqperf, the 48 h to 2026-11-03): the box-health owner got 95
messages from this watch. 52 were wall messages (38 digests, 14 immediate notes, 13 of the 14
repeated by a digest within 2 min; 41 of the 52 had no reaction); 39 were copies of session
NEAR and CLEARED messages (14 of the 20 alerts cleared within 4 min, and every clear said "No
action is needed"); 4 were resume messages, which stay. A NEAR alert was about 2 KB.

Expected after, to the box-health owner: wall messages about 3 a day, from about 26 (in the
journal's 40 h, 6 of 22 touches had no owning session and one 15-min window held 3); crossing
copies about 1 a day, from about 20 (2 escalations in 40 h); resume messages unchanged. Each
session gets what it got before, shorter. What this gives up: the box-health owner no longer
sees a single wall touch whose scope has a live owner, even an urgent one (anon above half the
peak, or an oom_kill), unless it is part of a burst; it learns of a near-OOM only when the scope
keeps rising or the kernel kills in it; it never sees a clear. The OOM kill is read from the
scope's `memory.events`, so a scope already gone when the next run reads it (every process in it
dead) is not reported as one.

Neither the chief of staff nor the INFRA lead hears a box alert (Sami, 2026-10-31): the INFRA
lead owned this detector and handed box health to the SRE.

A machine whose box health belongs to one session sets `BOX_HEALTH_OWNER` to that session id in
the service's environment: every message for the box-health owner then goes to that session
alone, with no role publish, while the session's own messages are unchanged — this is how the
OSS Verify lane VMs (`ossv-agent-*`) send their alerts to the fleet monitoring owner instead of
to this box's role holder. An invalid value stops the watch at start.

### Lane VMs: one digest an hour for the fleet

`--wall-digest-mode` decides how wall touches reach the box-health owner:

- **`send`** (default): as in the table above. sami-rocqperf, the devbox and any box with no
  collector run this way.
- **`collect`**: this box sends no orphan message and no burst. Every touch waits in the state
  file (`wall_pending`) until a collector drains it with `--wall-pending-json`, which prints the
  touches as JSON and clears them under the watch's own lock, and sends **one** message for the
  whole fleet, one line a scope (`wall_digest_text`: time, box, worker, peak, page cache,
  anonymous, session). Every lane VM runs this way: eight boxes each sending their own every 15
  minutes was 32 messages an hour into one session (Sami, 2026-10-31). The collector is
  `oss-verifier/agents/agentvm/wall-digest-collect.sh` on sami-rocqperf, hourly.

In collect mode two shapes do not wait for the hour: **anon at or above `--wall-urgent-anon` of
the peak** (default 0.50; anonymous memory is what the kernel cannot give back) and
**`memory.events` `oom_kill` above zero** (not a forecast at all). Each goes to the box-health
owner the moment it is seen, as one message naming the touch's own report.

## One alert per crossing

`/mnt/nvme/sami/tmp/omp-memory-watch/state.json` is the only memory, and the service owns it:

- held under an exclusive `flock` for the whole run, so a hand run and the timer can never both
  open the same crossing; the loser logs `another run holds …` and skips its period;
- keyed by the scope (a capped session) or scope + pid (an uncapped one), with the pid and its
  `/proc/<pid>/stat` start time, so a recycled pid starts a new crossing;
- `alerted_at` is written and `fsync`ed **before** the first message is sent;
- the **wall memory** (`walls`) is keyed by the scope name alone and never by pid: one scope
  holds several `omp` processes (the session and its js-eval helper), and a flip between them
  must not look like a new scope and report the whole backlog again. A key is dropped when its
  scope is gone, and `systemd-run --scope` never reuses a name;
- `wall_recent` holds the touches of the last `--wall-burst-window` and `wall_burst` the count
  the box-health owner was last told, in send mode; `wall_pending` holds the touches waiting for
  the fleet collector, newest last, at most `--wall-pending-max` (400), in collect mode. A send
  mode run drops `wall_pending` and `wall_digest_at` left by the version with a 15-min digest:
  every touch in them already reached its owner;
- `reports/` beside it holds every message's whole detail, 7 days;
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run`.

## Inputs

| Input | Source |
|---|---|
| omp processes and their cgroups | `/proc/*/comm`, `/proc/*/cgroup` |
| The ceiling | `memory.max` from the scope up to `/sys/fs/cgroup`, else `MemTotal` |
| The pressure | `memory.stat` (`anon`, `shmem`, `kernel`, `sock`), `memory.current`, `/proc/<pid>/statm` |
| The wall | `memory.events` (`max`, `oom`, `oom_kill`), `memory.peak`, `memory.swap.max` |
| Session id (alert only) | `/proc/<pid>/cmdline`, else `/proc/<pid>/fd` → `~/.omp/agent/sessions/**/*.jsonl` |
| Worker name and session (wall report only) | `.ossv-workers/<name>/` on a scope process's command line, and `<that dir>/session` unless `session_unconfirmed` is beside it |
| Top processes (wall report only) | `cgroup.procs`, `/proc/<pid>/statm`, `/proc/<pid>/cmdline` |
| Self-watch (alert only) | the command lines of the other processes in the scope, or of a process naming this pid |
| Listener | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Samples, open crossings, wall memory, recent touches, omp panes, resumes | `/mnt/nvme/sami/tmp/omp-memory-watch/state.json` |
| OOM kill during a crossing | `memory.events` `oom_kill` of the crossing's scope, against its value when the crossing opened |
| OOM bias | `cgroup.procs` of each capped omp scope, `/proc/<pid>/comm`, `/proc/<pid>/stat` (parent), `/proc/<pid>/oom_score_adj` (read, and written upward) |
| Panes | `tmux list-panes -a`, `/proc/<pid>/task/*/children` below each pane, an omp's open session file or `--resume` id, `docker exec` into one box when nothing else names its session |
| Whether a session ended on purpose | the last entry of its transcript, by `normal_exit_at` in `envoy-role-holder-watch` |
| Whether `agentbox move` owns a session's respawn, or gave up | `~/.cache/omp/agentbox/move-<id>.log` |

## Running it by hand

```sh
scripts/omp-memory-watch --dry-run --print-alert          # decide and print each message, send nothing
scripts/omp-memory-watch --fraction 0.70 --grace 60       # the unit's crossing defaults
scripts/omp-memory-watch --wall-fraction 0.98 --wall-cooldown 900 --wall-top 3   # wall defaults
scripts/omp-memory-watch --wall-burst 3 --wall-burst-window 900                  # burst defaults
scripts/omp-memory-watch --dry-run                         # also: bias counts it would raise, panes it would resume
scripts/omp-memory-watch --restart-dry-run                 # live alerts and bias, panes only named

# The tests: synthetic cgroup and /proc trees, no real /sys, /proc or tmux touched, python3 only.
python3 -m unittest discover -s scripts/tests -p 'test_omp_memory_watch*.py'
```

## Turning it off

```sh
systemctl --user disable --now omp-memory-watch.timer   # stop it
systemctl --user enable  --now omp-memory-watch.timer   # start it again
systemctl --user list-timers omp-memory-watch.timer     # when it next runs
journalctl --user -u omp-memory-watch.service -n 50     # what the last runs decided
```

Each action alone is turned off by adding its flag to `ExecStart` in
`omp/omp-memory-watch.service` and running `systemctl --user daemon-reload`: `--no-oom-bias`,
`--no-restart`, `--no-adopt-scopes`, or `--restart-dry-run` to keep the restarter naming panes
without resuming them.
An adj already raised stays raised until its process ends; to put one back by hand,
`echo 0 > /proc/<pid>/oom_score_adj` needs root (a lower value is a privileged write).

Every pane decision of the last hour:

```sh
journalctl --user -u omp-memory-watch.service --since -1h | grep -E 'RESUMED|would resume|: left:|wait:|kept:|forgotten|failed'
```

`installers/omp-memory-watch.sh` arms it (links `omp/omp-memory-watch.{service,timer}` into
`~/.config/systemd/user`). Deleting the state file forgets every open crossing: the next run
re-learns and re-alerts on one that is still over the threshold.
