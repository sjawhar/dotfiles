---
name: forward-browser
description: Use BEFORE any `browser` tool call meant to reach Sami's own Chrome (`app.relay: true` or a CDP URL), whenever a `browser open` hangs or returns "timed out", and before asking Sami to open, sign in to, connect, or "get on the relay" with his browser. The devbox drives Sami's laptop Chrome through the `forward` CLI, and that channel is LOCKED per session until `forward browser grant` is approved on his YubiKey — a relay timeout almost always means "no grant", not "Chrome isn't connected". The grant prints a CDP endpoint that the `browser` tool takes as `app.cdp_url`; `app.relay: true` does NOT use it and times out even with a live grant. Triggers - browser relay, drive Sami's Chrome, real login, signed-in dashboard, OMP Browser Relay, `Browser open timed out`, relay handshake, `forward doctor`, `forward browser grant`, `app.cdp_url`.
---

# Forward browser access

Sami's Chrome is reachable from this devbox through `forward`, the CLI that owns every
laptop↔devbox channel (URL opening, file preview, YubiKey, audio, and the browser relay).
The relay is **gated**: it stays locked for a session until Sami approves a grant on his
YubiKey. The `browser` tool does not know this. When the relay is locked it waits for an
extension handshake that can never arrive and reports a bare `Browser open timed out after
30000ms`, which reads exactly like "Chrome is not connected". It is not that.

## The rule

**Never diagnose a relay timeout by retrying it, and never ask Sami to connect his
browser.** Run `forward doctor` first. It names the state of every channel in one line each:

```
browser relay: locked at 100.100.92.97:12803 (no grant)
browser grant: none for this session — forward browser grant --ttl 30m
```

That pair means: the relay is fine, this session has no grant. Ask for one:

```bash
forward browser grant --ttl 30m
```

It blinks Sami's key and blocks until he taps it (about 20 seconds before it gives up with
`authorization timed out waiting for the YubiKey touch`). On success it prints the
session-local endpoint, e.g. `http://127.0.0.1:38987`. `forward doctor` then reads
`browser grant: live for this session at http://127.0.0.1:38987 (1779s left)`.

**That endpoint is a CDP discovery URL** (`/json/version` answers with Sami's Chrome and a
`webSocketDebuggerUrl`). Hand it to the `browser` tool as `app.cdp_url`; do not use
`app.relay: true`, which targets the OMP relay's own default port and times out even with a
live grant:

```json
{"action": "open", "name": "dash", "app": {"cdp_url": "http://127.0.0.1:38987", "target": "some-tab-substring"}}
```

`app.target` must match an existing tab's URL or title; the error lists every open tab when
it does not. To work in a tab of your own, adopt any tab and `browser.newPage()` inside `run`
rather than navigating Sami's visible tab. If `open` still times out, run `doctor` again, not
`grant` again: the next line down tells you which channel is at fault.

## Etiquette

- **The grant is a YubiKey touch, so it is a wait you never automate.** Ask once. If it
  times out, say so and stop; Sami taps when he is at the laptop. A key blinking for nobody
  is noise. Do not loop, poll, or re-issue on a timer.
- **Grants are per session and expire** (default 30 minutes). A timeout after an earlier
  success means the grant lapsed; `doctor` will say so. Ask for the TTL you actually need.
- **Asking Sami to act is the last step**, after `doctor` names a channel only he can fix
  (the laptop daemon down, the extension badge red). Present what `doctor` printed, not a
  guess.
- **You are inside his real, logged-in browser.** Name a target (`app.target`) or create
  your own tab; never navigate his visible tab uninvited; `close` when done.

## Reading `forward doctor`

| Line | Meaning | Do |
|---|---|---|
| `browser relay: locked … (no grant)` + `browser grant: none` | Normal locked state | `forward browser grant --ttl 30m` |
| `browser grant: live at http://127.0.0.1:<port> (Ns left)` | Ready | `browser open` with `app.cdp_url` = that URL |
| `browser relay:` unreachable / refused | Laptop daemon or Tailscale path down | Tell Sami what the line says |
| `url channel` / `callback bridge` / `pcsc` lines | Other channels; unrelated to browser access | Ignore for this purpose |

## Why this skill exists

A coordinator hit `Browser open timed out` four times over fifty minutes, decided Chrome
was not on the relay, and asked Sami to fix his browser — while `forward doctor` would have
printed `no grant` on the first try. The tool's own error names `omp browser-relay
install`, which is the wrong remedy on this machine. The fix was one command and one tap.
