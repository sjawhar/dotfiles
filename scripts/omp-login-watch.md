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
5. Alerts once per crossing, repeats every `--repeat` (default 30 min) while it is open, and
   clears when the provider has an enabled credential again and the transcripts are quiet. The
   crossing keeps **every** ask it opened, and the clear resolves each of them.

Only `errorMessage` counts, never the same words elsewhere in a transcript. Measured on
2026-11-01 at 16:38Z, with the login working: matching any line gave 18 "failing" sessions,
because agents were reading and writing about the morning's outage. The authoritative record is
the turn omp could not send (`"stopReason":"error","errorMessage":"No API key for provider: anthropic"`).

## Who hears it

| Path | How | What it is for |
| --- | --- | --- |
| **A person** | `dispatch ask` on dispatch://INFRA-387, urgency `blocking` | Only Sami can log in again. The ask opens inside a minute of the crossing and is resolved when the login comes back. A refused ask falls back to `dispatch message` on the same issue. |
| Agents | Envoy publish to `notifications.role.lead-box-health`, else a direct send to the box-health owner | The sessions that must stop working and wait |
| Agents, when the human path is down | the same publish, with `NOBODY WAS TOLD …` and the CLI's own error | The ask was refused and the message after it was refused too, or the unit has no `DISPATCH_SESSION_ID` to act as. The alert still follows it, and the ask is tried again at the next repeat. |
| Agents, when the watch cannot run | `omp-login-watch-failed.service`, the unit's `OnFailure=`, running `omp-login-watch --report-failure` | A pass that dies -- an unwritable state file, a killed run, a python error -- sends nothing, and the box then has no watch at all. The report names the unit, systemd's result and how long since the last completed pass. |

Each completed pass touches `heartbeat` beside the state file. `--report-failure` sends only when
that heartbeat is older than 300 s or absent, and then at most once per 1800 s while passes keep
failing (stamp: `failure-reported` in the same directory); a failure inside 300 s is only logged.
Why: 300 s is the watch's own budget to reach a person, so one late pass loses nothing. Under IO
pressure of 80-90 % on sami-rocqperf, 3 of 331 passes in 6 h hit `TimeoutStartSec=120` and the
next pass succeeded each time; each had sent a FAILED message. A unit with no completed run over
a longer window is unit-health-watch's report.

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
  crossing is still recorded, so the next repeat tries the ask again, and `NOBODY WAS TOLD …`
  goes to the box-health role with the CLI's error in it, because a person then has to be
  reached by hand.
- **The state file cannot be opened**: the pass sends no alert at all, since without that file
  it cannot tell a standing outage from a new one and would repeat every 60 s. It says that
  over Envoy and exits 1, which fires the `OnFailure` handler.
- **The pass dies any other way**: systemd runs `omp-login-watch-failed.service`, which posts
  one Envoy message naming the unit, `MONITOR_SERVICE_RESULT` and the exit status. It uses no
  dispatch CLI, because a watch failing on Dispatch must still be able to say so.

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
