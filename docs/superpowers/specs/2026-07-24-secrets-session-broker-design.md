# Secrets Session Broker (`secretsd`) — Design

**Date**: 2026-07-24
**Status**: approved. The daemon now lives in its own repo.
**Canonical copy**: `~/Code/secretsd/docs/design.md` — edit that one. This copy
is kept because the dotfiles-side integration (the `secrets` shim, the OpenCode
plugin, `.sops.yaml` recipients, and the migration ceremony) is implemented
here. Daemon implementation plan:
`~/Code/secretsd/docs/plans/2026-07-24-secretsd-daemon.md`.
**Supersedes**: the touch-per-window human-tier model (see
`docs/plans/2026-07-23-human-tier-secrets-redesign.md` for the full history of
what was tried and why it failed).

## Goal

Human-gated secrets (currently DEEL_API_KEY and PULUMI_CONFIG_PASSPHRASE)
**at grant time only** (one YubiKey touch), and remain available to that
session — and only that session — for the session's lifetime. Agent-tier
secrets keep working unattended with zero interaction, exactly as today.

### Threat model (what this defends against)

1. **Use while the user is away**: no new grant can be created without a
   physical YubiKey touch. An idle machine cannot mint access.
2. **Cross-session grant theft by string-claiming**: a session cannot obtain
   another session's grant by claiming its session ID. Authorization requires
   an unguessable per-session token issued by trusted harness code.
3. **Attribution**: every human-tier access is logged with session identity
   and caller details; every hardware interaction is announced before it
   happens.

### Accepted residual risks (stated plainly)

- **Same-UID reality**: all agents run as the same Unix user as the human. A
  *malicious* process can ptrace the broker, read `/proc/<pid>/environ` of a
  granted session, or read secrets out of a child process env. Kernel
  isolation between same-user processes does not exist. Per-session grants
  are therefore **workflow scoping and blast-radius control against sloppy or
  prompt-injected agents**, plus audit — not a hard security boundary. The
  hard boundary remains: no touch → no new plaintext.
- **Touch-cache window**: the YubiKey PIV touch cache is firmware-fixed at
  15s. Within 15s of a legitimate touch, one additional *pending, announced*
  decrypt could complete without a distinct touch. Mitigated by single-flight
  sequencing (below); the residual window is accepted for touch-only
  ergonomics. The future explicit-approve mode eliminates it.
- **Grant lifetime**: once granted, a secret stays available to its session
  until the session ends, `secrets lock`, or the 12h backstop — including
  while the user is away. This is deliberate: presence is proven at grant
  time, not continuously.
- **Open registration**: the broker socket is same-UID-open, so any process
  can register a token and pose as "a session". A forged registration cannot
  mint a grant — the touch requirement stands — but it can dress up a request
  with misleading metadata. A same-UID "private plugin channel" would be
  security theater (any such credential is readable by the same UID), so the
  defense is announcement hygiene: registrant-supplied metadata is labeled
  untrusted in announcements, and the registrant's peer PID/cmdline is
  logged.
- **Tokenless path abuse**: an agent that strips its own env can reach the
  interactive `(tty, boot-id)` flow. It still cannot get a grant without a
  touch, and tokenless requests are loudly labeled as such in the
  announcement — a tokenless announcement you didn't personally trigger is a
  deny. Best-effort hardening: the plugin registers PTYs it allocates and
  the broker rejects tokenless requests from them; ttys the plugin can't see
  (e.g. agent-created tmux panes) remain covered only by the labeling.

## Non-goals

- Per-use approval (schema leaves room; explicitly not wanted now).
- Per-key TTL policies (additive later; grant records carry created-at).
- Claude Code support for human-tier grants (dropped to simplify the design
  to a single authorization mechanism; CC keeps agent-tier access unchanged
  and gets a clear error for human-tier keys). Re-adding CC later means
  re-introducing a process-ancestry anchor path — a known, bounded extension.
- Hard isolation between same-UID processes.

## Architecture

```
agent (OpenCode session)
  │
  ├── MCP tool: secrets_request(KEY)      ── opencode plugin → broker socket
  │     (triggers grant flow; never returns secret values)
  │
  └── bash: secrets get KEY / secrets KEY -- cmd
        │
        ▼
   shims/secrets (bash, unchanged interface)
        │  agent-tier keys: sops decrypt directly, as today. No broker.
        │  human-tier keys: request over unix socket, sending the
        ▼  session token (read from a file referenced in env)
   secretsd  — Rust daemon, systemd user service, socket-activated,
        │     one per machine ($XDG_RUNTIME_DIR/secretsd.sock, 0600)
        │     plaintext cached only in daemon memory (mlockall, zeroize);
        │     clients and `-- cmd` children necessarily receive the
        │     plaintext of keys granted to them
        ▼
   YubiKey (touch = grant approval)  +  envoy (announcements)
```

### Components

1. **`secretsd`** (new, Rust): holds grants and decrypted human-tier values
   in memory; runs the approval state machine; performs sops decrypts;
   logs everything to journald.
   - Deps: std + `nix` (SO_PEERCRED for logging/UID check) + `zeroize`.
     No serde/dotenv crates on the plaintext path.
   - Hardening: `mlockall(MCL_CURRENT|MCL_FUTURE)` (fail closed if it
     fails), `PR_SET_DUMPABLE=0`, no core dumps, plaintext never in
     logs/errors, zeroized buffers, `Restart=on-failure`.
   - Input validation: key names must match `[A-Z][A-Z0-9_]*`; human-tier
     files opened via `openat` against the `secrets.human.d` directory with
     `O_NOFOLLOW`, regular files only.
   - Restart semantics: grants are memory-only and lost on restart. This is
     the correct security default. The shim reports "broker restarted;
     re-approval required" clearly.

2. **OpenCode plugin** (new, extends the existing session plugins): the
   identity authority.
   - On session create: generates a random 256-bit token, registers
     `(token, session_id, serve_pid)` with the broker, writes the token to
     `$XDG_RUNTIME_DIR/secretsd/<session>.token` (0600), and injects
     `SECRETSD_SESSION_TOKEN_FILE` (the path, not the value) into the
     session's bash env — so `env` dumps, transcripts, and child-env logs
     never contain the bearer credential itself.
   - Token lifecycle: the token is persisted in the plugin's session state
     and **re-registered on broker or serve restart** before requests are
     allowed. An unknown token is a hard identity error — it never falls
     back to the tokenless path.
   - Registers PTYs it allocates for sessions, letting the broker reject
     tokenless requests from known agent ttys (best effort; see residuals).
   - On session delete: notifies the broker → grants revoked,
     values zeroized when the last grant for a key dies. Event-driven; no
     `/proc` sweeps needed for the primary path.
   - Registers an MCP tool `secrets_request(key)` (OpenCode presents this to agents as `SecretsRequest`; instruct them by that name) → returns
     `granted | pending | denied | unavailable` plus human-readable
     guidance. Never returns secret values (they must not enter the
     transcript).

3. **`shims/secrets`** (modified): same CLI. The human-key set is derived
   from `secrets.human.d/*.env` filenames: those keys **always** route
   through the broker — a duplicate of a human key appearing in an
   agent-tier file is a fail-closed error, never a silent broker bypass.
   All other keys: agent tier, untouched. Human tier: connect to socket,
   send `{key, token}`, block up to 90s for the grant flow, print the value
   or a clear failure. The `/dev/shm` cache is deleted. No
   client-controlled file-path overrides — test configuration uses a
   separate broker socket/config path instead.

### Storage: recipients are the policy

The "tier" concept reduces to: **a key's sops recipient set is its access
class.** No policy file exists, so there is nothing for an agent to tamper
with — enforcement is cryptographic.

- **Auto (agent tier)**: `secrets.env` (committed), `secrets.local.env`
  (devbox, gitignored) — unchanged files, unchanged recipients (agent keys +
  YubiKey + recovery), unchanged zero-interaction consumers (voxtype, legion,
  skill MCPs, `dojo/.env` infrastructure).
- **Grant-required (human tier)**: `secrets.human.d/<KEY>.env` — one sops
  dotenv file per key, recipients: YubiKey + recovery **only**. Per-key files
  mean a grant decrypts only that key; broker memory never holds ungranted
  values. Key names remain listable without decryption (sops encrypts
  values, not names) — this also structurally fixes the known stray-blink
  bug in `secrets list`.
- Moving a key between classes = a deliberate re-encryption ceremony
  performed by the human (requires decrypting, which requires touch).
- An agent can corrupt or swap ciphertext files (same-UID write access) but
  cannot weaken access: after decrypt, the broker requires the file to
  contain **exactly one assignment whose name equals the requested key**,
  and rejects symlinks. Corruption is DoS only, visible in jj diffs.
- `.sops.yaml` **must gain a creation rule for `secrets\.human\.d/[^/]+\.env$`
  (YubiKey + recovery only) ordered before the default rule** — today the
  default rule carries agent recipients, so without this rule a new per-key
  file would silently be agent-decryptable, bypassing the broker entirely.
  The ceremony includes verifying that the agent key **fails** to decrypt
  every `secrets.human.d/*.env`.
- Key *names* are visible in the public repo (filenames). This is not new —
  sops never encrypted dotenv names in `secrets.human.env` either. Accepted.

## Authorization model

A grant is `(session_token, key)`, held in broker memory.

- Requests carry the session token, read by the shim from the file named in
  `SECRETSD_SESSION_TOKEN_FILE`. Authorization is a constant-time token
  comparison. No trust in claimed session IDs, process names, or ancestry.
- The token-file path is inherited by everything the session spawns (env),
  so session-owned background jobs (`setsid` etc.) keep working — scope
  follows the token, not the process tree.
- A sibling session in the same `opencode serve` process has a different
  token and cannot match another session's grant. Stealing a token requires
  actively reading another session's 0600 token file or memory — the
  accepted same-UID residual, a materially higher bar than claiming an ID
  string, and one that leaves no accidental trail in `env` dumps or
  transcripts.
- **Tokenless requests** (the human at an interactive shell): keyed on
  `(tty, boot-id)` as the grant scope; same touch flow; revoked when the tty
  vanishes. Tokenless requests are prominently labeled `TOKENLESS` in every
  announcement, and are rejected outright from PTYs registered as
  agent-session ttys. A tokenless announcement the user didn't personally
  trigger should be denied (see residual risks).
- Requests from other-UID peers: rejected (socket is 0600; SO_PEERCRED
  double-checks).

### Grant lifecycle

```
request → pending → decrypting → granted ──(session end | lock | 12h)→ revoked+zeroized
                        │
                        ├─ denied  (secrets deny / timeout 90s)
                        └─ failed  (YubiKey unreachable, decrypt error)
```

- State transitions are atomic with generation IDs. Deny/timeout kills the
  decrypt child process group and discards late output. Concurrent waiters
  are coalesced **only** for the same `(token, key)`; one session's approval
  never creates another session's grant.
- Revocation triggers: plugin session-end event (primary), `secrets lock`
  (wipe all + revoke all), 12h backstop per grant, serve-process death
  (broker holds a pidfd per registration as a fallback for crashed serves).
- Grants are never persisted. Killing the harness process revokes its
  grants; reopening the same on-disk session re-registers the persisted
  token but starts with zero grants — the first human-tier request goes
  through a fresh announce-and-touch. One touch to resume is deliberate:
  a new process is a new presence proof.
- Plaintext lifetime = union of active grants for that key; zeroized when
  the last grant dies.

## Approval UX

1. Agent requests a human-tier key (via tool or blocked shell command).
2. Broker creates the pending request and **announces before any hardware
   interaction**: AGENT NOTICE on the requester's stderr (agent relays
   in-session — the primary path, since human-tier use is interactive),
   `notify-send` locally on the laptop, envoy event
   (`notifications.secrets.request`) → Slack as belt-and-braces. Every
   announcement contains: the **key name**, a **broker-generated request
   ID**, and the **verified scope type** (token-verified session vs.
   TOKENLESS tty); registrant-supplied metadata (session ID, cmdline) is
   included but labeled untrusted. The decrypt starts only after an
   announcement channel acknowledges submission; if every channel fails,
   the request stays pending (no unannounced blink) until timeout.
3. The YubiKey blink is the approval prompt; **one touch completes the
   grant**. `secrets deny <id>` or the 90s timeout rejects it.
4. **Single-flight**: at most one YubiKey operation in flight; the broker
   waits out the 15s hardware touch cache before starting the next pending
   decrypt, so one touch can never approve two requests.
   Per-scope pending-request limits with backoff after deny/timeout prevent
   one hostile or broken session from flooding the single-flight queue and
   locking out legitimate grants; `secrets deny` and `secrets lock` are
   always serviced immediately, ahead of the queue.
5. Devbox: approval requires the YubiKey tunnel. If unreachable, fail fast
   with "YubiKey unreachable — connect via the devbox wrapper", not a hang.
6. Repeat access within a granted session: instant, silent, no hardware
   interaction.

Future (config-flip, no architecture change): explicit `secrets approve
<id>` mode — approval runs in the human's TTY, which is what pinentry
needs, so PIN+touch (`pin-policy` change) becomes possible per-key.

## CLI / tool surface

```
secrets get KEY / secrets KEY -- cmd    unchanged for all callers
secrets list                            names only; never decrypts
secrets grants                          active grants + pending requests
secrets deny [id]                       reject a pending request
secrets lock                            wipe all plaintext, revoke all grants
secrets approve [id]                    reserved for explicit-approve mode
MCP: secrets_request(key)               grant flow trigger; no values returned
```

## Migration

1. Add the `secrets.human.d/` creation rule to `.sops.yaml` (YubiKey +
   recovery only, ordered before the default rule) and a
   recipient-verification check (agent key must fail to decrypt any file
   under `secrets.human.d/`). **This precedes any file creation.**
2. Build + install `secretsd` and the plugin on both machines (installer +
   systemd user units; binary fits the `bin/` convention; `cargo-deny` in CI
   posture per mise setup).
3. One-time ceremony **on the laptop**: split `secrets.human.env` into
   `secrets.human.d/<KEY>.env` (YubiKey decrypts; re-encrypt to YubiKey +
   recovery only; recovery key stays in 1Password and never transits the
   devbox). Run the recipient-verification check. Confirm no human key
   also exists in an agent-tier file. `dojo/.env` is agent-tier and
   untouched — no key ceremony.
4. Update `shims/secrets`: remove `/dev/shm` cache, add socket path +
   human-key routing (filenames win), keep AGENT NOTICE behavior on
   failure.
5. Verify (section below), then remove `secrets.human.env`.
6. sops files never merge textually (existing rule; per-key files also
   shrink conflict surface).

## Verification

- Agent tier unattended (must not regress):
  `ssh devbox '~/.dotfiles/shims/secrets get ANTHROPIC_API_KEY | wc -c'` — no
  interaction, no broker involvement.
- Grant flow: from an OpenCode session, `secrets get DEEL_API_KEY` →
  announcement → one touch → value; second call instant; new session →
  new announcement.
- Cross-session isolation: session B requesting a key granted to session A
  gets its own pending request, never A's value.
- Revocation: end the session → `secrets grants` empty → next request
  requires touch again. `secrets lock` same.
- `secrets list` produces zero YubiKey interaction (strace-verifiable).
- Recipient check: agent age key fails to decrypt every
  `secrets.human.d/*.env`.
- Duplicate-key bypass: a human key planted in `secrets.env` → shim fails
  closed, never serves the agent-tier copy.
- Token-strip: unset `SECRETSD_SESSION_TOKEN_FILE` inside an agent session
  → request is labeled TOKENLESS (or rejected if the PTY is registered),
  never inherits the session's grant.
- Broker restart mid-session → clear re-approval message, no hang.
- Recovery path unchanged: `SOPS_AGE_KEY=$(op ...) sops -d
  secrets.human.d/<KEY>.env` on the laptop only.

## Future extensions (explicitly deferred)

- Per-use approval mode and per-key TTLs (grant records already carry
  created-at; additive).
- Explicit-approve + PIN mode (see Approval UX).
- Claude Code human-tier support via process-ancestry anchoring.
- Migrating agent-tier storage to per-key files / per-key recipient sets
  (e.g., laptop-only keys) — the "recipients are the policy" model already
  covers it; pure churn today.
- Envoy-mediated remote approval (approve from phone/Slack when away from
  the YubiKey) — deliberately out: touch-at-grant is the presence proof.
