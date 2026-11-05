# omp-login-watch

Tells **a person** when this box's omp sessions have lost model access and only a login brings
it back. Every other watch here alerts agents; this one cannot, because every agent is stopped
by the thing it reports.

```sh
omp-login-watch                       # one pass, as the timer runs it
omp-login-watch --dry-run --print-alert   # decide and print, send nothing
omp-login-watch --db /tmp/copy.db --print-alert   # read a copy (forces --dry-run)
```

## What happened, and why this exists

On 2026-11-01 at 12:35:17Z the shared Anthropic OAuth credential in `~/.omp/agent/agent.db`
(row 7) was disabled with
`oauth refresh failed: OAuthError: anthropic-console token refresh failed: 400 {"error": "invalid_grant", "error_description": "Refresh token not found or invalid"}`.
Every session on sami-rocqperf that used the `anthropic` provider stopped. The transcripts hold
141 `OAuth token refresh timed out` errors in 17 sessions between 12:34:42Z and 12:35:36Z, then
860 `No API key for provider: anthropic` in 67 sessions until 16:18:15Z. The box ran with no
model for 3 h 45 min; a person noticed and logged in again at 16:20:42Z (row 8). Credential 6
died the same way on 2026-10-25 at 22:15:38Z, so the condition recurs
(dispatch://INFRA-387).

## What one pass does

1. Reads the credential store **read-only** (`file:…?mode=ro`, `PRAGMA query_only`), and only
   the columns `id, provider, credential_type, identity_key, disabled_cause, created_at,
   updated_at`. The `data` column holds the tokens: no query here names it, and
   `scripts/tests/test_omp_login_watch.py` fails if one ever does.
2. A **model provider is down** when it has credential rows and every one carries a
   `disabled_cause`. A provider name with `:` in it is an MCP server (`mcp_oauth:profile:…`),
   not a model provider, and is skipped.
3. Counts the live `omp` processes that read **this** store. A session started with
   `--profile NAME`, or with `PI_CODING_AGENT_DIR` elsewhere, reads its own store — the pbc
   profile's `~/.omp/profiles/pbc/agent/agent.db` kept working all through the 12:35Z outage —
   so it is neither counted nor reported as stopped. A `-p --mode json` run carries no session
   id; it loses the provider the same way and counts as affected.
4. Reads what the sessions themselves report: each live session's transcript written inside
   `--window` (default 300 s) is tailed (`--tail-bytes`, default 256 KiB) and its
   **`errorMessage` values** are matched against `No API key for provider: X`,
   `OAuth token refresh failed for provider X`, `invalid_grant` and `authentication_error`.
   `--sessions` distinct live sessions (default 3) inside the window is a crossing on its own,
   which catches a provider whose credential still looks enabled while every call is refused.
5. Alerts once per crossing and again only on a **new fact**: another credential of the
   provider disabled, the sessions' own errors joining a crossing the store opened, or twice as
   many sessions reporting them. It clears when the provider has an enabled credential again
   and the transcripts are quiet. The crossing keeps **every** ask it opened, and the clear
   resolves each of them.

### Finding the transcripts without walking the session tree

A pass used to list all of `~/.omp/agent/sessions` (1,655 directories on 2026-11-03) to find
the transcripts written in the window. That walk is the step that killed passes: 13 of 1,819
passes in the 40 h to 2026-11-03 01:50Z hit `TimeoutStartSec=120`, all in IO storms (10:23-10:37Z
on 11-02 at io some 87-96 % with 776 tasks blocked; 00:29-00:35Z on 11-03).
Killed passes printed nothing; the
passes that finished took 6 s at p50, 41 s at p95 and 79 s at p99 with 1.2 s of CPU, so they
were waiting, not working.

Now each live session's transcript is, in order: the `.jsonl` its omp holds open
(`/proc/<pid>/fd`, in-kernel readlinks, 31 of 35 sessions on 2026-11-03), the path found on an
earlier pass (`transcripts.json` beside the state file), or, for a session known neither way, a
walk that stops once it has found them all. A session the whole walk did not find is not walked
for again for 10 minutes. The credential store, the processes, Envoy, the transcripts and their
tails share a 30 s budget per pass, counted from the moment the process started, so a pass always
reaches its decision, its state and its heartbeat. Measured at load 250 on 2026-11-03 at the
unit's `Nice=15`: 3.7 s for a pass that walked for 4 new sessions, then 0.6-2.6 s; the old walk
alone took 0.1-2.2 s at that load. Each pass logs its seconds per phase (`store, processes,
envoy, transcripts, tails, cache; start-up`) and how each transcript was found, so the next slow
pass names its slow step.

### A read that never comes back

That budget was a clock the steps checked between two reads, and a read the box has stalled
never comes back to look at it. The pass started 09:40:33Z on 2026-11-03 was killed at 120 s
having logged nothing, and the one started 07:37:56Z spent 62.9 s in Python's start-up before
its budget began to count (81.8 s in all). So now:

| Step | Runs | Hard limit |
|---|---|---|
| the credential store | forked child | 10 s (`STORE_SECONDS`), or the evidence budget left |
| the process scan (`/proc/<pid>/{comm,cmdline,environ}` of every omp) | forked child | 15 s (`PROCESS_SECONDS`), or the budget left |
| the transcript lookup (held fds, cached paths, the walk) | forked child | the budget left, plus 2 s for it to say it stopped |
| the transcript tails | forked child | the budget left, plus 2 s |
| each dispatch CLI call | forked child running the CLI | `--dispatch-timeout` (60 s), or what is left of the pass's 100 s (`RUN_SECONDS`) less 16 s for the Envoy sends |
| the failure report's journal read (`--report-failure`) | forked child | 25 s, inside that unit's 60 s |

A child that has not answered at its limit gets SIGKILL and is not waited for; one the kernel
holds in a read goes with the unit's cgroup when the pass exits, and a dispatch child closes its
copy of the state file's lock first, so the next pass is never left waiting on it. A cut
evidence step is one line (`  the process scan: cut after 15 s, still running …`). When the
credential store itself was cut, the pass then opens, repeats and clears nothing, writes no state
and no heartbeat, and exits 0. When the store was read and a later step was cut, the store's own
fact still stands: a provider whose every credential is disabled opens its crossing, and the ask
and the Envoy message say that session evidence was not read this pass, in place of a count of
affected sessions. Nothing the sessions decide is done on that pass -- no crossing on their
errors, no new fact, no retry, no clear -- and it writes no heartbeat. The next pass reads it
all again a minute later. A pass with no room left for the dispatch CLI does not take the step
that needs it -- `CROSSING anthropic waits for the next pass` -- rather than recording an ask
that never went out. And a pass whose walk or tails stopped at the budget opens crossings
(unread transcripts can only hide errors) but clears none: `CLEAR anthropic held: … quiet is not
established`.

What it gives up: a session whose omp does not hold its transcript open and that moved to a new
transcript file keeps being read at its old path, so its errors count only once its new file is
found by the walk (when the old path is gone) or held open.

Only `errorMessage` counts, never the same words elsewhere in a transcript. Measured on
2026-11-01 at 16:38Z, with the login working: matching any line gave 18 "failing" sessions,
because agents were reading and writing about the morning's outage. The authoritative record is
the turn omp could not send (`"stopReason":"error","errorMessage":"No API key for provider: anthropic"`).

## Who hears it

| Path | How | What it is for |
| --- | --- | --- |
| **A person** | `dispatch ask` on dispatch://INFRA-387, urgency `blocking` | Only Sami can log in again. The ask opens inside a minute of the crossing and is resolved when the login comes back. A refused ask falls back to `dispatch message` on the same issue. |
| Agents | Envoy publish to `notifications.role.lead-box-health`, else a direct send to the box-health owner | The sessions that must stop working and wait |
| Agents, when the human path is down | the same publish, `NOBODY WAS TOLD …` and the CLI's own error | The ask was refused and the message after it was refused too, or the unit has no `DISPATCH_SESSION_ID` to act as. Said once until an ask goes through; the ask is tried again every `--repeat` (30 min), and the one that goes through is one `PERSON ASKED:` line. |
| Agents, when the watch cannot run | `omp-login-watch-failed.service`, the unit's `OnFailure=`, running `omp-login-watch --report-failure` | A pass that dies -- an unwritable state file, a killed run, a python error -- sends nothing, and the box then has no watch at all. The report names the unit, systemd's result and how long since the last completed pass. |

Every Envoy message is at most 600 characters and ends with `Full report: <path>`, a file in
`reports/` beside the state file (kept 7 days) with the whole text: the full alert as the
person reads it, the Dispatch result, the asks a clear resolved, or the unit's last 30 journal
lines. When the state directory is what cannot be written, the report goes to
`$TMPDIR/omp-login-watch/reports/`. The Dispatch ask itself keeps the whole text.

Each completed pass touches `heartbeat` beside the state file. `--report-failure` sends only when
that heartbeat is older than 300 s or absent, and then **once per failure episode**: a failure
after the report, with no completed pass since, is the same fact and is only logged (stamp:
`failure-reported` in the same directory). The next completed pass ends the episode; it sends
nothing, so a reader sees the recovery in `systemctl --user status omp-login-watch`. Why: 300 s
is the watch's own budget to reach a person, so one late pass loses nothing. On 2026-11-02
10:24-10:37Z, before the 300 s grace, seven killed passes in a row sent eight identical messages
in 13 minutes. Replayed over the journal's 13 failed passes of 2026-11-02 09:50Z to 11-03 02:00Z,
the grace alone sends 2 (10:29Z and 00:36Z; the kills at 18:18Z, 22:15Z and 22:55Z came 122-224 s
after a completed pass); this rule sends the same 2. It differs in an episode longer than
30 min, which the grace's 30-min repeat said again and again with nothing new, and in a second
episode inside 30 min of the first, which that repeat did not say at all. Expected: about 1 a
day (from 7), and fewer once passes stop dying.

Both run from a systemd user unit with **no model call**: the dispatch CLI is a bun program
that needs `DISPATCH_HOST`, a `DISPATCH_SESSION_ID` (the unit's drop-in, written once per
machine by the installer) and bun on `PATH`; the Envoy POST is one HTTP request.

The alert names the provider, every disabled credential by id with its identity and cause, the
count of affected sessions, and the single action:

```sh
omp login anthropic
```

`omp login <provider>` is the terminal counterpart of `/login`. It writes a new credential into
the shared store, and the running sessions pick it up.

A cause string is printed with any token-looking run replaced by `<redacted>` before it leaves
this box.

### When it cannot tell anybody

A watch that fails quietly is the outage it exists to end. Each way this one can fail now
reaches the box-health role, and nothing here is only a journal line:

- **The ask and its fallback message both fail**, or the CLI has no identity to act as: the
  crossing is still recorded, so the ask is tried again every `--repeat`, and `NOBODY WAS TOLD …`
  goes to the box-health role once with the CLI's error in it, because a person then has to be
  reached by hand.
- **The state file cannot be opened**: the pass sends no alert at all, since without that file
  it cannot tell a standing outage from a new one and would repeat every 60 s. It says that
  over Envoy once per failure episode (stamp `state-failure-reported`, beside the state file or,
  when that directory cannot be written, in `$TMPDIR/omp-login-watch/`) and exits 1, which fires
  the `OnFailure` handler.
- **The pass dies any other way**: systemd runs `omp-login-watch-failed.service`, which posts
  one Envoy message per failure episode naming the unit, `MONITOR_SERVICE_RESULT` and the exit
  status. It uses no dispatch CLI, because a watch failing on Dispatch must still be able to say
  so.

Measured on 2026-11-01 at 22:35:44Z against a local listener: the handler posts
`POST /v1/messages/publish` to `notifications.role.lead-box-health` and stops there; a listener
that refuses the publish gets the direct send to the box-health owner instead.


## Arming it

```sh
BOX_HEALTH_OWNER=<session id> ~/.dotfiles/installers/omp-login-watch.sh
systemctl --user list-timers omp-login-watch.timer
```

The installer refuses a machine with no shared credential store, no bun, no dispatch CLI or no
`envoy.json`. It links `omp-login-watch-failed.service` beside the watch's own unit, since
systemd refuses nothing for a missing `OnFailure` target and would simply stay silent. It
writes the watch's Dispatch identity once and never replaces it: a new id would orphan the
open asks it has to resolve.

To turn it off: `systemctl --user disable --now omp-login-watch.timer`.

## What it does not do

- It never logs in, refreshes, writes or repairs a credential. It reads, and it tells.
- It never reads the `data` column, and it keeps no copy of the store.
- It does not watch a provider whose key comes from configuration (`omp/models.local.yml`
  gives `openai`, `ossv-gateway` and `vertex` an `apiKey`): such a provider has no row in
  `auth_credentials`, so there is no login for a person to repeat.
- A non-default `--state` or `--db` forces `--dry-run`: a run reading another box's copy must
  never send, because its memory of what was already said is not this box's.
