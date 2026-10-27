# envoy-role-holder-watch

Every 30 s, checks that each Envoy role whose holder session is on **this** machine still has a
holder process, and alerts over Envoy when it does not.

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

## What it checks

One run, with no transcript reading in the decision and no model call:

1. `GET /v1/sessions` — every session Envoy lists live, with its roles, host and dir. Roles whose
   holder is on this machine go into the state file, so a holder that later drops out of the
   registry is still watched.
2. For a holder with **no** process, `GET /v1/roles/<role>` — the durable claim, which says
   whether the role is still registered and, for a lapsed claim, whether that read released it.
3. The holder's process: an `omp` process whose `/proc/<pid>/cmdline` names the session id, or
   whose open descriptors hold `~/.omp/agent/sessions/**/<stamp>_<session id>.jsonl`. A pid is
   never remembered between runs — pids on this box wrap within a day, so a remembered pid comes
   back as an unrelated process.
4. **The process is the only liveness test.** A role alerts when its claim is registered and no
   process holds the session, for longer than the 60 s grace. Envoy's registry never raises an
   alert on its own: under load it drops sessions that are running (Rocq speed recorded
   `no live session` for live sessions on 2026-10-25 15:10-15:16Z). The opposite disagreement — a
   live pid Envoy does not list — is logged as a registry fault and alerts nobody.

Timing: death + 60 s grace + at most one 30 s period, so about a minute.

## What it does not check

- Sessions that hold no role, and role holders on other machines. Role holders only.
- A **hung** session. The process is the test, so a session whose process lives but whose agent
  loop has stopped looks healthy here. In the 2026-10-27 case the transcript stopped at 05:52:19Z
  while the process lived until 05:55:01Z; this watch would have fired on the process, not on the
  hang.
- Whether anyone acts. It alerts, repeats once as an escalation after 10 minutes, and alerts once
  more when the role is live again. It restarts nothing and kills nothing.

## Inputs

| Input | Source |
|---|---|
| Live sessions, their roles, host, dir, topics | `GET $ENVOY/v1/sessions` |
| Role claim and its release | `GET $ENVOY/v1/roles/<role>` (only for a holder with no process) |
| Holder process | `/proc/*/comm`, `/proc/*/cmdline`, `/proc/*/fd` |
| Last seen alive | `st_mtime` of the holder's `~/.omp/agent/sessions/**/<stamp>_<id>.jsonl`, read once when the incident opens and carried unchanged through every message about it |
| Handover files (alert text only) | absolute `.md` paths the holder's transcript names whose basename reads like a handover, plus `WAKE.md`/`HANDOVER.md`/`HANDOFF.md`/`STATE.md` in its working directory, `/mnt/nvme/sami/<lane>` and `/mnt/nvme/sami/tmp/<lane>` |
| Listener and bearer | `$ENVOY_CONFIG_FILE`, else `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json` |
| Roster and open incidents | `/mnt/nvme/sami/tmp/envoy-role-holder-watch/state.json` |

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

A clear is sent once, and names what ended the incident: a pid holds the session again (with the
pid), a new holder claimed the role, or the stale claim was released while the lane is still down.

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

Each alert names the role, the holder, the pid that is gone, when it was last seen alive, how
long the role has pointed at a dead holder, that the claim is still registered, the restart
command (`omp --resume <id>` in a tmux pane in window `agents:3`), the handover file to brief it
from (or "no handover file found"), and how to clear the claim by hand:

```sh
curl -X DELETE $ENVOY/v1/sessions/<holder>   # drop the dead registration
curl $ENVOY/v1/roles/<role>                  # this lookup releases the lapsed claim
```

Envoy has no route that deletes a role claim directly; the replacement session takes it with
`envoy_role_set` (`soft: true`), and the listener's own reaper clears it within ~5 minutes of the
holder's registration expiring.

## Running it by hand

```sh
scripts/envoy-role-holder-watch --dry-run --print-alert   # decide and print, send nothing
scripts/envoy-role-holder-watch --grace 60 --escalate 600 # the unit's defaults
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
