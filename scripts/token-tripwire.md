# token-tripwire

Tells a session, over Envoy, the moment secretsd stops knowing its token.

Armed with `installers/token-tripwire.sh`; runs as the user service `token-tripwire.service`.

## Why

On 2026-11-07 a session-interview script, since deleted, resumed a copy of a
live session **under that session's own id**. secretsd keys its registry on the id alone, so the
copy displaced the live token and its exit unregistered it. **59 of 153 live box sessions were
left unable to fetch a secret, with no error anywhere until each one next asked**, and it went
unnoticed for a day. Nothing resumes a live session's transcript any more: the skill librarian
interviews a session through omp's read-only seance consult (`task` with `agent: "seance"`),
which reads the saved transcript and never becomes the session. dispatch://INFRA-1644.

## Why a tripwire and not a report

**The state cannot be enumerated.** `secrets` has no registry dump, and the failing log line
carries no session id — `untrusted_registered_session=None` is exactly what being unknown looks
like. Measured over the whole of 2026-11-07: 1,429 requests resolved to a verified session
across 1,108 session ids, and **nine pids had an UNKNOWN that never verified, every one of them
already exited**. By the time anyone looked, there was nobody left to tell.

So the only instrument that can name an affected session is one that is already watching when
the request fails, while the pid is alive.

## How

A healthy request logs **twice** for one pid: `scope_kind=None` with `decision="UNKNOWN_TOKEN"`,
then `scope_kind=Some(VerifiedSession)` naming the session. A broken one logs only the first.

Following `journalctl -t secrets -f`, on an UNKNOWN with no verified line for that pid within two
seconds, it reads `/proc/<pid>/cgroup`, resolves the container to its box, reads that box's
record for the session id, and sends that session one Envoy note with the one-call recovery
(`secrets_request` for any agent-tier key, no restart). One note per session per hour.

**It never acts for the session.** A credential belongs to the session that holds it.

## Two things that will bite the next watch

- **A cgroup names the container by ID, not by box name**:
  `…/user-1000-agentbox.slice/docker-<64 hex>.scope/init.scope`. The first version looked for
  `agentbox-` in the cgroup, found nothing, and skipped every event in silence.
- **A systemd unit has no login shell**, so `envoy` was not on its `PATH` and the send died with
  `FileNotFoundError`. Both are covered by `scripts/tests/test_token_tripwire.py`.

## A long quiet period is the expected result

It fires only when a session's token is already broken. **Silence means the fork bug is staying
fixed, not that the watch is idle** — the thing it watches for was invisible for a day precisely
because nothing was watching. Do not remove it for being quiet; check it with a synthetic
journal line instead:

```bash
p=$(docker top <a box> -eo pid | awk 'NR==2{print $1}')
systemd-cat -t secrets printf '%s\n' "2026-01-01T00:00:00.0Z  INFO request handled key=TEST \
source=- peer_uid=65534 peer_pid=$p caller_pid=Some($p) scope_kind=None \
untrusted_registered_session=None registered_root_pid=None request_id=None decision=\"UNKNOWN_TOKEN\""
journalctl --user -u token-tripwire -n 3
```

That is how it was proved on 2026-11-07: breaking a real token would have cost a live session its
credentials, so the trigger is synthetic and the pid is real.
