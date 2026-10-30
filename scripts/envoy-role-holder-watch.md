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
   | `process` | an `omp` process naming the session on its command line, in an open transcript's **file name**, or in that transcript's **header** (`{"type":"session","id":…}`, which is the second record, not the first) |
   | `openers` | any process **this user can read** holding the session's transcript open, matched by device and inode, so a lane under a wrapper or a renamed binary still counts and one inside an agent box (uid-shifted `/proc`) does not. Read **only** when the first three came back silent — which is also the only time it can change the answer — so a healthy role never pays its cost, and an open incident keeps paying it every period, since it is the one source that can see a lane running under a name this watch does not know, and so the one that can clear such an incident |
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

Cost of a run: every transcript is resolved through a **single** walk of the session root
(34,272 files, 1.2 s here), not one glob per role. Measured at the unit's `Nice=15` and idle IO,
23 roles and 56 panes: **2.6-3.6 s** with every holder seen, and **12 s** with every holder
unseen — the degenerate case, where each one costs a header pass and a scan of every process's
descriptors. `TimeoutStartSec` is 25 s; a run systemd kills is a watch that has stopped watching
while its timer still looks healthy.

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
- Whether anyone acts. It alerts, repeats once as an escalation after 10 minutes, and alerts once
  more when the role is live again. It restarts nothing and kills nothing.
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
When nothing resolves the session, it says so and names the directory to read.

The watcher never runs that command. `/pause` freezes omp with a process-local gate. It writes no
paused state to the session JSONL or `$HOME/.omp/agent` state, so a later shell prompt is
indistinguishable from a failed agent. An automatic restart could revive a session Sami paused
before its shell appeared. The alert therefore says `Automatic restart: NOT RUN` until omp
persists a pause marker the watcher can check.

It goes to the INFRA lead and the lane's stream lead — the live holder of a `lead-*` or
`stream-lead-*` role whose working directory is the session's — and a `Told:` line names each
recipient **with the role it was reached by** (`stream lead (lead-oss-verify) 01a14b36-…`), so a
reader knows who else saw it. When no stream lead resolves, the alert says so.

**A pane left at a shell is treated as a death. A deliberate stop closes its pane.** There is no
allow-list, no intentionally-down flag, nothing to expire — a suppression entry nobody expires is
exactly how a real death hides (OSS Verify lead, 2026-10-27, who asked for one and then preferred
this rule). The failure mode the rule guards against is a lead who stops a lane and walks away
leaving its pane open: that pane is indistinguishable from a crashed one, so it alerts. Closing
the pane costs one `tmux kill-pane` and removes the lane from this check entirely.

One alert per pane per incident, and one clear — when omp runs in the pane again (naming the
pid), or when the pane is gone.

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
| Holder process | `/proc/*/comm`, `/proc/*/cmdline`, `/proc/*/fd` |
| Last seen alive | `st_mtime` of the holder's `~/.omp/agent/sessions/**/<stamp>_<id>.jsonl`, read once when the incident opens and carried unchanged through every message about it |
| Handover files (alert text only) | absolute `.md` paths the holder's transcript names whose basename reads like a handover, plus `WAKE.md`/`HANDOVER.md`/`HANDOFF.md`/`STATE.md` in its working directory, `/mnt/nvme/sami/<lane>` and `/mnt/nvme/sami/tmp/<lane>` |
| Listener and bearer | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Roster and open incidents | `/mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json` |
| Panes | `tmux list-panes -a -F` on the default server: session, window index and name, pane id and index, current command, pane pid, path, title |
| Pane foreground | `/proc/*/stat`: `comm`, `ppid` and `tpgid` of every process, once per run |
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
the source and what it found), or a new live session now lists the role.

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

Each alert names the role, the holder, how long nothing has seen it, and one line per source
saying what that source answered. Then a line that reads, in these words:

> ESTABLISHED: I cannot see this session. NOT established: that the holder has stopped.

and then the remedy, in order, stopping as soon as one answers:

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
   that order two minutes after a false alert, are what took `bet64-eta-arity`'s claim. Tell the
   INFRA lead the role and the holder and let the claim lapse on its own. Agent infra is adding
   an explicit `POST /v1/roles/release` that records who released it; when it is deployed, the
   one constant `RELEASE_CALL` in the script becomes that call — its wording is already in the
   comment above it — and nothing else in the text moves.

## Running it by hand

```sh
scripts/envoy-role-holder-watch --dry-run --print-alert   # decide and print, send nothing
scripts/envoy-role-holder-watch --grace 60 --write-grace 120 --escalate 600 --pane-grace 180  # the unit's defaults
```

## Turning it off

```sh
systemctl --user disable --now envoy-role-holder-watch.timer   # stop it
systemctl --user enable  --now envoy-role-holder-watch.timer   # start it again
systemctl --user list-timers envoy-role-holder-watch.timer     # when it next runs
journalctl --user -u envoy-role-holder-watch.service -n 50     # what the last runs decided
```

`installers/envoy-role-holder-watch.sh` arms it (links `omp/envoy-role-holder-watch.{service,timer}`
into `~/.config/systemd/user`). Deleting `/mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json`
forgets every open incident: the next run re-learns the roles and re-alerts on one that is still
dead.
