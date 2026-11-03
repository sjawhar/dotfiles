# envoy-role-holder-watch

Every 30 s, two checks on this machine's agent lanes:

1. each Envoy role whose holder session is here still has a holder **process**;
2. each agent **tmux pane** still has omp running in it.

## Why it exists

On 2026-10-27T05:52Z the Verified Linux owner session
(`01a1798a-9911-7231-a1bb-cf29d7985273`, role `lead-verified-linux`) stopped inside a `wait`. It
wrote no error and no exit record; its systemd user services kept running, so the lane looked
alive; its Envoy role claim stayed registered, so messages to
`notifications.role.lead-verified-linux` looked deliverable. A peer first learnt of it at
06:03:46Z from a `no live session` send error, 11 minutes later. About 40 in-session subagent
lanes were lost. (dispatch://INFRA-602)

A role claim is durable and outlives its holder: the claim lives in the `envoy_roles` KV bucket,
the holder's registration in `envoy_sessions` with a 5-minute TTL
(`packages/envoy/internal/store/kv.go`, `internal/session/registry.go`). Between the holder's
death and the claim's release, the role points at nobody and nothing says so.

## The second incident, 2026-10-29

At 09:20:30Z this watch alerted that role `bet64-eta-arity` pointed at a dead holder, session
`01a176d3-4178-7593-bfe8-eadd6dd54f54`. The holder was **alive**: pid 724296, up 17,353 s, pane
`%49`, three subagents, its transcript open. At 09:22:31Z the role lost its claim and the session
became unreachable by Envoy. The watch issued no write; the damage came from its own remedy text,
which told the reader to run `DELETE /v1/sessions/<holder>` and then `GET /v1/roles/<role>`, and
the reader did, two minutes later.

Three defects, all fixed here:

1. **The liveness test read the wrong two places.** It looked for the session id on an `omp`
   command line or in a transcript's **file name**. `omp --resume` takes a path as well as an id,
   and a lane that began as a subagent is resumed from a path named after the subagent
   (`…/<stamp>_<parent id>/ArityLane.jsonl`): no id on the command line, none in the file name,
   only in the file's own header. `transcript_of` had the same blind spot and never descended
   into the subagent directories at all.
2. **The message could not tell "this session is dead" from "I cannot see this session"**, and
   reported the first. Only the second is ever establishable from here.
3. **The remedy text was unsafe twice**: `omp --resume <id>` in a new pane puts a second process
   on a transcript the first still holds (INFRA-681), and `GET /v1/roles/<role>` releases a
   lapsed holder's claim as a side effect of reading it, which is what took this claim. It still
   does: `resolveLiveRoleHolder` calls `releaseExpiredRoleClaim` on sjawhar/legion `origin/main`
   at `d157fc868`, and the live listener answers **405** to the `POST /v1/roles/release` that
   will replace it (both checked 2026-10-29 09:50Z). The remedy therefore names no release call
   at all yet, and says why.

## What it checks

One run, with no model call:

1. `GET /v1/sessions` — every session Envoy lists live, with its roles, host, dir and the
   timestamp of its last heartbeat. Roles whose holder is on this machine go into the state file,
   so a holder that later drops out of the registry is still watched.
2. Four sources on each holder, each of which can say *found* and none of which can say *gone*:

   | Source | What it reads |
   |---|---|
   | `process` | an `omp` process that RUNS the session, by one of three things only: its `--resume`/`--restore` target (the id, or the header of the file a path names), a transcript it holds open **for writing** (`O_WRONLY`/`O_RDWR` in its `fdinfo` flags, the rule of omp-dup-watch), by that file's **name** or its **header** (`{"type":"session","id":…}`, the second record). Never from other words of its command line, never from a transcript it only reads, and never from an omp started with `-p`/`--print` or `--no-session`, which runs no session of its own: from 06:48Z to 07:13Z on 2026-11-03 the skill librarian's annotation batch ran `omp -p … --no-session … @…/known/runs/opus48/01a19366-…-s20/task.md` four times, and this watch read each run as NICKMINE (01a19366) running elsewhere. An omp in an agent box counts: its comm and command line are readable from the host, only its descriptors are not, so its resume target is the one source. So does an **agent box launcher** naming the session (`agentbox new … -- omp --resume <id>`, `agentbox-run.ts … --resume <id>`, matched on argv words): it lives exactly as long as the box, and it is the only process of a session between an `agentbox move` respawn and its box's omp starting (15 min at load 410 on 2026-11-02) |
   | `openers` | any process **this user can read** holding the session's transcript open **for writing**, matched by device and inode, so a lane under a wrapper or a renamed binary still counts, a tool that only reads the file does not, and one inside an agent box (uid-shifted `/proc`) does not. Read **only** when the first three came back silent — which is also the only time it can change the answer — so a healthy role never pays its cost, and an open incident keeps paying it every period, since it is the one source that can see a lane running under a name this watch does not know, and so the one that can clear such an incident |
   | `transcript` | how long ago the session's transcript was written. Inside `--write-grace` (120 s) something is writing it |
   | `registry` | Envoy's own entry and heartbeat: `GET /v1/sessions`, confirmed for an absent holder with `GET /v1/registry/<id>`. Both only read |

3. **It calls no role route.** `GET /v1/roles/<role>` is how Envoy releases a claim whose holder
   has left the session bucket (`cmd/listener/role_holder.go` `resolveLiveRoleHolder` →
   `releaseExpiredRoleClaim`), and that holder is exactly the one anyone would read this claim
   for: the read is a write. Agent infra is making it pure and adding an explicit
   `POST /v1/roles/release`; neither is deployed (checked 2026-10-29 09:50Z, above). Even once
   it is pure the watch will leave it out, because its answer adds nothing to the verdict — the
   holder of a claim is the session the watch has already failed to find — and what that costs
   is below.
4. **A holder is only ever UNSEEN, never dead.** A role alerts when **every** source has been
   silent for the 60 s grace. One silent source — most often the registry, which drops live
   sessions on a loaded box inside its 300 s TTL — is logged and never alerts, never opens an
   incident and never asks anyone to restart anything. Every message says which of the two it
   established, and `guard_verdict` refuses to send one that claims the holder is dead.

Timing: last sighting + 60 s grace + at most one 30 s period, so about a minute.

Cost of a run, and its budget. A holder a process or a fresh heartbeat shows is never looked up
further: its transcript is read only when those two are silent. Then the transcript is the one
the watch already knows the session by — the file an omp held open for it, this run or an
earlier one, kept in the state file — at the cost of one stat. Next is the session directory of
the working directory Envoy lists for the holder (`~/.omp/agent/sessions/<encoded dir>/*_<id>.jsonl`),
one listing, every time: that is where omp writes a session's transcript. Only a session found
in neither needs the walk of the session root, and that walk and the header pass behind it share
one **3 s** budget per run (`TRANSCRIPT_WALK_SECONDS`), go through the working directories
newest first, say so in the transcript row when they run out, and are not repeated for the
same holder for 10 minutes after a miss. A transcript on no disk here is looked up in the
session sweep's record, below. The descriptor scan behind `openers` is one per run, for every
holder. The pane check runs before the holder checks, so a long period still checks every pane.

Five things keep a period inside its time, each measured on this box:

- **One pass over `/proc`.** `/proc/<pid>/stat` of every process gives both the `omp` processes
  and the pane check's parent/foreground table, where two passes read 3,400 `comm` files and
  3,400 `stat` files (0.2-0.5 s of CPU, up to 9 s of wall clock each at load 268).
- **The transcript headers are cached between runs**, in `headers.json` beside the state file,
  keyed by path and inode. A header is written when the transcript is created and never
  rewritten, so only a transcript opened since the last run is read at all. Re-reading all ~500
  that the omp processes hold open was **38 s of a 39 s process scan** at load 268, one read
  5 s, and the slowest step of 49 of the 71 runs over 45 s in the six hours before this change.
  A run reads new headers for at most `HEADER_READ_SECONDS` (10 s); the rest are counted, the
  summary line says so, and **no holder is judged unseen in a period that left any unread** —
  one of them could be the sighting.
- **A finished incident costs the two free sources only.** A recorded normal exit or an alert
  quieted on the directory rule has one question left, "did the session come back", which a
  process or a heartbeat answers; the session-root walk, the directory walk, the descriptor
  scan and the Postgres read are not repeated. One such holder — `bet-owner-nickmine`, which
  exited on 2026-11-02 and whose transcript the sweep then deleted — cost the run of
  2026-11-03T00:18:07Z **80 s of its 90 s**, and that run was killed.
- **The run has a wall-clock budget** (`--budget`, 60 s) counted from the moment the process
  started, under the unit's 90 s `TimeoutStartSec`. Past it the run stops between two units of
  work — never inside one — writes the state file with everything it has decided, logs one line
  saying where it stopped, and exits 0, so systemd records no failure and the next period starts
  with the roles this one did not reach (`checked_at`, oldest first). A run that starts with its
  budget already spent still does one unit of work, so every period moves at least one role
  forward.
- **Every disk and `/proc` read runs in a forked child under a hard limit** (`bounded_step`):
  `STEP_SECONDS` (15 s) or the budget left, whichever is less, and the budget left for the
  process scan. The budget above is a clock the run checks between two units of work, and a
  read the box has stalled never comes back to look at it: the three runs started 07:14:50Z,
  07:19:21Z and 07:20:56Z on 2026-11-03, at io some 87 % with 305 tasks blocked, were killed at
  90 s having logged nothing, while the runs between them took 2-12 s. A child that has not
  answered at its limit gets SIGKILL and is not waited for; one the kernel holds in a read goes
  with the unit's cgroup when the run exits, and it closes its copy of the state file's lock
  first, so it cannot make the next period skip. The read is then **cut**: the pane or the role
  it was for is not judged this period — no alert, no clear, its journal state unchanged — with
  one line (`<role>: holder <id> not judged this run: <step>, cut after N s …`,
  `pane <id> <where>: not judged this run: …`), and the run goes on to the next one. A cut
  process scan stops the run as the budget does, since a half table would make live holders
  look unseen; a cut `tmux list-panes` skips the pane check and not the roles. The summary line
  names every cut read. A child sends back the memo tables it filled (known transcripts,
  headers, the session-root index), so the run goes on with what it learnt.

Not bounded this way: Python's own start-up (the budget counts it), the state file and
`headers.json` (read and written by the run itself, on `/mnt/nvme`, as the decision record),
`envoy.json`, and the Envoy calls, which have their own 8 s timeout.

Measured interleaved against the unchanged script on 2026-11-03 02:42-03:36Z, 24 runs each,
alternating, at load 199-379 and at this unit's `Nice` and IO class, reading the live box and
sending nothing: **median 7.71 s → 1.49 s, p90 18.0 s → 2.53 s, p95 19.1 s → 3.46 s, worst
19.3 s → 8.12 s** (that worst new run was one `GET /v1/sessions` taking 8.1 s, and nothing
else), CPU **0.96 s → 0.41 s**, journal lines **42.5 → 1.3 per run**.

In the six hours to 02:36Z the unchanged script, as the journal records it, had a median run of
20.7 s and a p95 of 62.6 s, and failed **35 of 545 runs (6.4 %)**: 34 killed at the 90 s
`TimeoutStartSec`, every one of them in the processes or the roles phase, and one that could not
read `/v1/sessions`. With the budget alone, the 691 runs from 04:19Z to 10:10Z on 2026-11-03
still lost **3 to the 90 s kill (0.4 %)**; each logged nothing before it, as a run held in one
read does, and such a read is now cut at its own limit. What is left is the listener
unreachable, which exits 2 and should be visible, and a stall outside any step: Python's
start-up (up to 21 s in those runs), or journald and systemd answering late after the summary
line (up to 46 s).

Unbounded, the walk was every run on 2026-11-02 from 09:00Z: at load 410-455 the box's root disk
answered it in 247 s and the header pass in 325 s, and every run was killed at its then 25 s
`TimeoutStartSec` before any holder after the first, any pane, alert or clear. The summary line
gives each phase's seconds (`envoy`, `processes`, `panes`, `roles`). A oneshot still starting
absorbs the next timer tick, so runs cannot pile up.

## What a run writes to the journal

**One line per run**, plus a line each time a pane or a role CHANGES state, plus the alerts and
clears, which are events. The summary line carries the counts, each phase's seconds, whatever
ran out, and the suppressions in their short form.

The journal held **150,188 lines in 40 h** (23,597 in the six hours to 2026-11-03T02:36Z, of
which 10,723 were `<role>: ok (holder …)` and 8,318 `pane %N …: agent pane runs bash, not omp
…; holding` — the same sentences every 30 s). The state behind each line is now kept in the
state file under `logged`, keyed by pane and by role, and a line goes out only when that state
differs from the one recorded: a new pid, a new command in the pane, a grace that has started, an
alert, a clear. A pane or role the run never reached keeps its recorded state, so a budgeted run
does not make the next one repeat everything.

Expected volume after, at this box's ~120 runs an hour: **about 160 lines an hour** from the
watch (1.3 a run measured over 24 runs) against about 3,700, plus systemd's own three lines a
run, which this file does not control. Messages are unchanged in number — the review of
2026-11-03 classed all of this watcher's alerts in 48 h as true positives, and nothing here
changes when one is sent — and each is at most 600 characters instead of up to 2,404.

## What these bounds give up

- **A message alone no longer carries the evidence.** The per-source lines, the warning about
  the claim, the handover files and the list of who else was told are in the report file, which
  lives on the box that sent the message. A reader on another machine sees the decision and a
  path.
- **The journal no longer shows the current state of every pane and role.** `journalctl -n 50`
  shows what changed; the state of each pane and role right now is
  `jq .logged /mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json`.
- **A finished incident is watched by the two free sources only.** A holder that comes back
  invisible to both an `omp` process and Envoy's registry — resumed inside an agent box, say —
  is not noticed until the role is dropped from the state file 24 h after it was last seen.
- **A run that reaches its budget checks some roles a period later.** Detection then takes the
  60 s grace plus up to two 30 s periods instead of one. No run in the 24 measured came near the
  budget; the roles left over are the next run's first.

## What not reading the claim costs

Not calling `GET /v1/roles/<role>` gives up three things:

- **the claim's holder**, so the watch no longer learns from Envoy that a role moved to a session
  it cannot see. It reads that from `/v1/sessions` instead — a live session that now lists the
  role — which covers every case except a new holder that has also lapsed out of the registry;
- **whether the claim is still registered at all.** An alert therefore says the claim *may* still
  point at the holder, rather than that it does;
- **`reason=holder_lapsed` and Envoy's final `last_seen`** for a holder already out of the
  registry, which was the one diagnostic the lookup added.

None of the three changes who is alerted or when. All three become one safe read for whoever
acts on an alert the day the lookup stops releasing the claim.

## What it does not check

- Sessions that hold no role and have no pane, and role holders on other machines.
- Panes on another tmux server than the default one, and a pane inside an agent box whose box is
  gone (its pane is a shell like any other, and the restart command it gets is the resurrect
  `agentbox new … -- omp --resume <id>` line when one was saved).
- A **hung** session. The process is the test, so a session whose process lives but whose agent
  loop has stopped looks healthy here. In the 2026-10-27 case the transcript stopped at 05:52:19Z
  while the process lived until 05:55:01Z; this watch would have fired on the process, not on the
  hang.
- Whether anyone acts. It alerts, repeats once as an escalation 10 minutes or more after the
  first alert — a message that says it is a repeat of the incident first seen at `<time>`, first
  alerted at `<time>`, and that no further repeat follows — and alerts once more when the role is
  live again. It restarts nothing and kills nothing.
- **A lane whose process this user cannot read.** Inside an agent box `/proc/<pid>/fd` is
  uid-shifted, so neither the process source nor `openers` can see it; both answer "not found"
  and say so in those words. That is one more reason an alert may say only that it cannot see
  the holder.
- **A transcript whose `{"type":"session"}` record is past its 8th line, or behind a line longer
  than 64 KB.** `session_id_in_header` stops there.

## Agent panes left at a shell

After the 2026-10-27T13:56Z reboot, five of six Verified Linux panes came back as a bash prompt:
tmux-resurrect restored the layout, but the `omp --resume` it saved never ran. Nothing above sees
that — no process died, and only one of the six held an Envoy role — and a count of `omp`
processes cannot see it either, since the processes were never started.

Every pane of the **default** tmux server is read once per run
(`tmux list-panes -a`, panes de-duplicated by `pane_id` because grouped sessions list each pane
once per group member):

- An **agent pane** is one whose title omp set (it starts `π `), or one this watch has already
  seen omp running in (remembered in the state file, keyed by tmux server pid, server start time
  and pane id, so a restarted server never inherits the old memory), or one with an `omp` among
  the descendants of its pane process.
- It is **at a shell** when its pane process is a shell (`bash`/`sh`/`zsh`/`fish`/`dash`) *and* is
  its own terminal foreground process group (`tpgid == pid` in `/proc/<pid>/stat`) — nothing is
  running in it at all. A pane busy with some other command is logged, never alerted.
- After the **180 s** grace (`--pane-grace`) that is one alert. A pane whose resolved session is
  running in a process somewhere else is never an incident.

The session the pane last ran is resolved in this order, and the alert says which one answered:

1. the session this watch itself saw omp holding in that pane;
2. the newest top-level transcript in the pane's working directory
   (`~/.omp/agent/sessions/<encoded cwd>/*.jsonl`) whose own title — the `title` of its first line
   — appears in the pane title, and whose session no process holds;
3. the newest `tmux_resurrect_*.txt` save (last 60) whose line for this pane's place
   (session, window index, pane index) under the same title holds an `omp … --resume …`
   command. The session is taken from that command's `--resume` **argument**: a uuid as itself,
   a `.jsonl` path by reading that file's own header. Not from the last uuid anywhere in the
   command — on 8 of this box's panes the resume argument is a path whose directory carries the
   *parent's* uuid, so that reading named a different, live session, and the alert's own
   "is anything holding this transcript?" check then looked at the wrong file and cleared a
   restart that would have put a second omp on a running lane.

The alert names the pane and its place, how long it has been at a shell, and the evidence (shell
pid and its foreground group, the pane title). It then carries one named line of its own,
`LAST TRANSCRIPT WRITE: <time> (<age> before this alert)` — the fact that separates a planned
wind-down from a crash: OSS Verify read `00:51:59Z` off the first of these alerts and recognised
its own planned stop. After it come the session and its source, the transcript, the handover
files, and the restart as a ready command: `tmux send-keys -t %57 'omp --resume <id>' Enter`.
When nothing resolves the session, it says so, names the directory to read, and adds one line
saying what that directory **does** hold: the number of transcripts there and, for the three
newest, the session id, its title, and whether an omp process holds it now (`live in pid N` or
`not running`). On 2026-11-01 pane `%450` (`/home/sami/src/eng-ops`) was told no transcript
carried its title while the directory held exactly one, of a live session, so a reader had to
look the directory up to learn the association was wrong and not missing.

The watcher never runs that command, and the alert says `Automatic restart: NOT RUN`. It never
restarts that pane automatically, because a paused pane and a dead one look the same to a
script. The person or lead who reads the alert decides from the pane.

It goes to the INFRA lead and the lane's stream lead — the live holder of a `lead-*` or
`stream-lead-*` role whose working directory is the session's — and a `Told:` line names each
recipient **with the role it was reached by** (`stream lead (lead-oss-verify) 01a14b36-…`), so a
reader knows who else saw it. When no stream lead resolves, the alert says so.

**A pane counts only when its last omp died without meaning to.** There is still no allow-list,
no intentionally-down flag, nothing to expire — a suppression entry nobody expires is exactly how
a real death hides (OSS Verify lead, 2026-10-27). What the watch reads instead is what the
session itself recorded. A pane at a shell is **not** an incident when:

1. **its session ended on purpose**: the last entry of its transcript is a normal
   `session_exit`. On 2026-11-03 review panes `%2177` and `%2176` alerted at 07:04Z and 07:06Z,
   three minutes after their sessions (01a1a031-90db, 01a1a031-9157) exited normally;
2. **its omp was a one-shot**: started with `-p`/`--print`, which processes its prompt and exits;
3. **an `agentbox move` owns its session**: `~/.cache/omp/agentbox/move-to/active/<session id>`
   exists and its `pid=` is alive (a stale file whose relauncher died holds nothing), or the
   session's `agentbox-moved` marker (in its transcript directory, or
   `~/.omp/agentbox-moved/<session id>`) names this machine as `from=` — the rule omp-memory-watch
   applies before it resumes anything. `%1740` and `%1741` alerted at 05:12:57Z and 05:35:37Z
   while their sessions were being moved to sami-devbox. A move's hang-up writes no
   `session_exit` (01a19e3c-8da0's transcript ends in an aborted turn at 05:32:08Z), so a move
   that fails and leaves the pane down is still caught once its active file goes.

The session a pane is judged by is the **top-level** session its omp runs (its `--resume`
target, else the one top-level transcript it holds open), never a subagent's: `%1740` was
remembered as running 01a19ed8, an advisor subagent of the moved 01a19e3c-8d64.

**What it still catches: an omp that died without a normal exit** — a crash, a kill, an
out-of-memory, a hang-up with nothing respawning it, a session that never started — in a pane
that no move owns. A lead who stops a lane by killing it, rather than letting it exit, still gets
an alert.

**One alert per pane per session, until omp runs in that pane again.** A pane whose session is
seen running in a process outside it is no longer that lane's pane: the clear says so, and the
pane is not watched again until omp runs in it. On 2026-11-03 pane `%40` (session 01a19366,
NICKMINE, which closed itself on purpose at about 07:05Z) cleared at 06:48Z and 07:04Z and
alerted at 06:54Z and 07:09Z: each time an annotation run that only NAMED the session started,
the watch read it as the session running elsewhere and closed the incident, and each time the
run ended the pane opened a new incident and alerted again. The runs no longer count as the
session (see the `process` source); the once-per-session rule holds even when something does.
The same holds for
any other way an incident closes and reopens — a shell child that comes and goes — because the
lane's state has not changed, and neither has the alert its reader already has. One clear goes
out when omp runs in the pane again (naming the pid), when the pane is gone, or when one of the
three rules above applies.

### A pane whose tmux session is `mv-*` is a move, not a death

`ossv-agent move` relocates a worker in a tmux session of its own, named `mv-<worker>`. It stops
the worker on this box — its pane prints `[Stopped by SIGTERM]` and the mover's recovery lines —
pushes the tree, and resumes **the same session id** on another box. That is exactly the shape
this check fires on, and the restart it would hand a reader would put a second omp on a session
the mover is about to resume elsewhere. Three such alerts went out on `ossv-agent-open` between
20:14Z and 20:35Z on 2026-10-30.

So a mover pane is **held, not alerted**, for `--move-grace` (default 30 minutes). It is held
rather than dropped because a move that never finishes is a real failure: past that window the
alert says `WORKER MOVE NOT FINISHED`, names the mover session and the worker, says where the
worker runs now — `<home>/work/workers/<worker>/MOVED.md` first, else the `vm` column of
`<home>/work/cold/workers.tsv` — and names **no restart command at all**. A move with no record
says the worker's new box is not recorded yet rather than guessing one.

### The owner of a lane worker is not a role

A lane-worker pane has no `lead-*` holder in its own directory, so its alert used to fall
through to the INFRA lead and the lane-box operator. `ossv-worker` records the real owner in
`<home>/work/.ossv-workers/<lane>/lead`, and the lane name is in the pane's own working
directory. That id is a **dated claim** — the lead it names can itself have been restarted
under a new id — so it is checked against Envoy's live sessions before it is used. Live, it is
told and the alert says the owner came from ossv-worker's record and not from a role; not live,
it is named in the alert and not told. The operator still gets the alert as well: it holds the
tmux socket the restart command needs.

## The two checks, and what neither covers

This watcher now holds two independent checks for two different deaths:

| The lane | Covered by |
|---|---|
| holds a role, pane gone or alive | the role check (its holder has no process) |
| no role, pane alive with no agent in it | the pane check |
| **no role, and its pane is gone** | **nothing here** |

A lane whose pane is gone **and** whose transcript is gone is undetectable by this watcher. That
is not hypothetical: the OSS Verify repo lane
(`01a14b84-6cd3-71af-918c-db534cd8d762`) died on 2026-10-27 with no pane on this box or its VM,
and a full-disk search on both found nothing of it but its Dispatch client state — no transcript,
so neither check, and no file, has anything to key on.

The durable fix is therefore not more watcher: **every lane holds an Envoy role**, and the role
check then covers it however its pane ends. The repo lane is being re-dispatched holding the role
`ossv-repo` precisely to close its own case.

## Inputs

| Input | Source |
|---|---|
| Live sessions, their roles, host, dir, topics | `GET $ENVOY/v1/sessions` |
| Role claim | **not read** by the watch, and named in no remedy: `GET /v1/roles/<role>` releases a lapsed holder's claim as it answers; see "What not reading the claim costs" |
| Envoy registry entry and heartbeat | `GET $ENVOY/v1/sessions`, and `GET $ENVOY/v1/registry/<id>` for a holder absent from that list (`sessionEntryHandler` reads the session bucket and answers it; it makes no release) |
| Holder process | `/proc/*/stat` (one pass, shared with the pane check), then `/proc/*/cmdline`, `/proc/*/fd` and `/proc/*/fdinfo` of the `omp` and launcher processes only |
| Last seen alive | `st_mtime` of the holder's `~/.omp/agent/sessions/**/<stamp>_<id>.jsonl`, else `mtime_ms` of its row in Postgres when the sweep took it, read once when the incident opens and carried unchanged through every message about it |
| Swept transcript | the session sweep's record `~/.local/share/omp-session-sql/deleted.tsv` (which files it deleted, and when), then the last two 1 MiB parts of the row in `omp_session_files` on agents-postgres, through `psql` and the sweep's DSN file beside that record; read once per holder (5 s limit) and kept in the state file under `swept` |
| Handover files (alert text only) | absolute `.md` paths the holder's transcript names whose basename reads like a handover, plus `WAKE.md`/`HANDOVER.md`/`HANDOFF.md`/`STATE.md` in its working directory, `/mnt/nvme/sami/<lane>` and `/mnt/nvme/sami/tmp/<lane>` |
| Listener and bearer | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Roster, open incidents and the journal state of each pane and role | `/mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json` |
| Transcript headers of the last run | `/mnt/nvme/sami/tmp/envoy-role-holder-watch/headers.json`, keyed by path and inode |
| The full text of each message sent | `/mnt/nvme/sami/tmp/envoy-role-holder-watch/reports/<stamp>-<kind>.txt`, pruned at 7 days |
| Panes | `tmux list-panes -a -F` on the default server: session, window index and name, pane id and index, current command, pane pid, path, title |
| Pane foreground | the same `/proc/*/stat` pass: `comm`, `ppid` and `tpgid` of every process, once per run |
| Pane's last session | this watch's own memory, else the title of the first line of each `*.jsonl` in the pane's `~/.omp/agent/sessions/<encoded cwd>/`, else `~/.local/share/tmux/resurrect/tmux_resurrect_*.txt` |

Reaching the default tmux server takes two things a shell has and a systemd service does not, so
the unit sets both: `TMUX_TMPDIR=%h/.tmux/sockets` (the socket directory `.bashrc` exports) and a
`PATH` with `~/.mise/shims` on it. `/usr/bin/tmux` is 3.4 against this box's mise-managed 3.8
server and answers `server exited unexpectedly`; the run logs that line with the `TMUX_TMPDIR` it
used rather than reporting zero panes in silence.

## One alert per incident

That state file is the only memory, and the service owns it:

- It is held under an exclusive `flock` for the whole run, so a hand run and the timer can never
  both open the same incident; the loser logs `another run holds …` and skips its period.
- An incident is keyed by role **and** holder session, so a role re-claimed by a new session is a
  new incident and the old one cannot re-fire.
- `alerted_at` is written and `fsync`ed **before** the first message is sent, so a crash between
  the two cannot resend it.
- Everything a message quotes — first detection, last seen alive, the pid checked — is fixed when
  the incident opens, so the alert, the escalation and the clear all agree.
- `--state <other path>` is for tests only: a run on any other file forces `--dry-run` and sends
  nothing. Two state files are what sent two "first alert" messages for one holder on
  2026-10-27 at 07:53:20Z and 07:55:18Z.

A clear is sent once, and names what ended the incident: a source can see the holder again (with
the source and what it found), a new live session now lists the role, or the holder exited
normally.

A holder exited normally when the **last** entry of its transcript is
`{"type":"custom","customType":"session_exit","data":{"kind":"normal",…}}`. The watch logs
`<role>: holder exited normally at <recordedAt>` once, sends the clear to whoever an earlier alert
reached, records `exited_reported_at` on the incident, and never alerts or escalates on that holder
again. Any other last entry is still an incident. On 2026-11-01 sessions 01a1798a-9911 and
01a1798a-995f in `~/src/machcsl-riscv` exited this way at 22:46:26Z and 22:46:56Z and were
escalated as if they had stopped.

### A recorded exit holds when the transcript moves

Once read, the exit on the incident is the record, not the transcript. On 2026-11-02 the holder
of `bet-owner-nickmine` (01a19366-958c, a design lane its owner ended on purpose) exited
normally at 05:41:32Z, and the watch recorded it at 05:47:45Z. Two things then took the
transcript out of reach, and each time the watch acted as if no exit had ever been read:

- 07:50:22Z `omp-transcript-offload` replaced the file with a symlink into `/mnt/durable`. At
  13:20:57Z, in the first run to finish since 09:00Z, the 3 s walk of the session root ran out
  before it reached that directory. No exit was found, the incident had no `alerted_at`, and a
  first alert went to the chief of staff, the INFRA lead and TASKCOACH.
- 14:08:13Z the session sweep (dispatch://OSSV-240) imported the transcript into Postgres and
  deleted it at 14:10:26Z. At 14:10:46Z the watch escalated, labelled "STILL UNSEEN after 10
  minutes", 49 minutes after the alert.

These were the only two messages: per-role counts of every alert, escalation and clear since
2026-10-31 show no role alerted twice for one holder except by this path. Now:

1. An exit on the incident holds whether or not the transcript can be read again. Only the
   session writing its transcript after the exit (a resume) undoes it, logged as `it was
   resumed`; a resumed session the watch sees running closes the incident anyway.
2. The holder's own session directory is listed before the walk, so a holder the watch never saw
   running is found by one listing.
3. A transcript on no disk here is looked up in the sweep's record and read back from the table,
   so a holder that ended before the watch first looked still reads as exited. When it is not an
   exit, the alert's step 3 gives `omp --resume <path>` by full path, which is the only resume
   a session held only in Postgres supports (dispatch://INFRA-754).
4. An incident whose alert went out after its exit was recorded gets one clear, to everyone the
   alert reached, saying that alert was wrong and why.

A planned stop is recognised by the session's own exit record, which omp writes as it closes.
There is still no planned-stop list a lead fills in: a stop that does not end in that record (a
kill, a closed pane) looks like a crash here, and the rule in "A pane left at a shell is treated
as a death" applies to it.

## Who hears about it

Agent sessions only, never a person:

- the chief of staff (`01a13dca-e979-7264-af32-f654ef57a6f5`) and the INFRA lead
  (`01a13c57-d826-7242-80fe-5f0670188b11`);
- the dead role's **stream lead**: the live holder of a `stream-lead-*` or `lead-*` role in the
  same working directory. When none resolves, the alert says so;
- the lane's **peers**: live sessions sharing a `notifications.team.*` topic with the dead
  holder, and one line published to each of those topics. Envoy keeps no readable record of who
  recently messaged a role, so this is the closest cheap substitute; when the dead session had no
  team topic, the alert says that the peers could not be named.

Every message is at most **600 characters** and ends with `Full report: <path>`. The message is
the decision — which role, which holder, how long, what is and is not established, and the two
steps that cost nothing and cannot hurt a live lane — and the report file holds the whole alert:
one line per source, the warning about the claim, the handover files, who else was told, the
suppressions and their reasons. A pane alert of **2,404 characters** went out on
2026-11-01T10:01Z and its first 200 characters were what its reader acted on.

Reports go to `<state dir>/reports/<UTC YYYYmmddTHHMMSSZ>-<kind>.txt`
(`/mnt/nvme/sami/tmp/envoy-role-holder-watch/reports/` here), and each run deletes the ones
older than seven days. The report is written and the message names it even when no `Full report`
file could be written, so a reader always sees the path the text should be at.

Each alert names the role, the holder, how long nothing has seen it, and — in the report — one
line per source saying what that source answered. Both carry the line that reads, in these words:

> ESTABLISHED: I cannot see this session. NOT established: that the holder has stopped.

and then the remedy, in order, stopping as soon as one answers (the report carries all four;
the message carries the first two):

1. **Look at the pane** this watch last saw the session running in, or the `tmux list-panes`
   command that finds it by title or directory.
2. **Ask the session** — type into that pane, or `envoy send <holder> "are you alive?"`. A
   session that answers is alive and the alert is wrong. This is how the 2026-10-29 09:20:30Z
   false alert was caught two minutes in, and it is first because it costs nothing and cannot
   hurt a live lane.
3. Only once a person has looked and found no live lane: read the tail of the transcript and
   restart the lane **in its own pane, never a new one** — two omp processes on one transcript is
   the INFRA-681 double loop — with the handover files the alert names.
4. Release the claim. The alert names **no call** for this today, and says why: `GET
   /v1/roles/<role>` releases a lapsed holder's claim as it answers, and
   `DELETE /v1/sessions/<holder>` unregisters a session that may be alive. Those two, run in
   that order two minutes after a false alert, are what took `bet64-eta-arity`'s claim. Let the
   claim lapse on its own. Agent infra is adding
   an explicit `POST /v1/roles/release` that records who released it; when it is deployed, the
   one constant `RELEASE_CALL` in the script becomes that call — its wording is already in the
   comment above it — and nothing else in the text moves.

## Running it by hand

```sh
scripts/envoy-role-holder-watch --dry-run --print-alert   # decide and print, send nothing
scripts/envoy-role-holder-watch --grace 60 --write-grace 120 --escalate 600 --pane-grace 180 --budget 60  # the unit's defaults
```

## Turning it off

```sh
systemctl --user disable --now envoy-role-holder-watch.timer   # stop it
systemctl --user enable  --now envoy-role-holder-watch.timer   # start it again
systemctl --user list-timers envoy-role-holder-watch.timer     # when it next runs
journalctl --user -u envoy-role-holder-watch.service -n 50     # what the last runs decided
```

`installers/envoy-role-holder-watch.sh` arms it (links `omp/envoy-role-holder-watch.{service,timer}`
into `~/.config/systemd/user`). Beside the state file the watch keeps two things it can lose
without harm: `headers.json`, the transcript headers of the last run, and `reports/`, the full
text of each message it has sent in the last seven days. Deleting
`/mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json` forgets every open incident and every
journal state: the next run re-learns the roles, logs each one's state once and re-alerts on a
holder that is still unseen.
