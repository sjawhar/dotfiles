# Handoff: Human-Tier Secrets — Design Revisit

**Status**: current design works mechanically but fails the user's workflow.
**Ask**: revisit the design for human-gated secrets. The tiering *concept* may
survive; the touch-per-window interaction model does not. Read the constraints
before proposing anything — most "obvious" fixes were tried and died on a
constraint below.

## The actual goal (in the user's terms)

1. Secrets must not sit in plaintext on the devbox (the original complaint).
2. Unattended agents (both machines) must decrypt the secrets they legitimately
   need with **zero interaction, ever** — this part works and must not regress.
3. A small set of sensitive secrets (DEEL_API_KEY, PULUMI_CONFIG_PASSPHRASE,
   FLEET_LICENSE_KEY) should require *some* human involvement — but the current
   involvement (YubiKey touch every ≤5 minutes) interrupts real work sessions.
   The user does interactive agent sessions using DEEL for 30–90 minutes;
   "locked again" mid-flow is the failure mode that killed this design.
4. Interaction must be **predictable**: a stray key-blink from a background
   process is a bug, not a feature.

## Hard constraints (learned the expensive way)

- **Agent shells are TTY-less.** Anything that prompts (PIN, passphrase,
  pinentry) fails there. This killed pin-policy=once.
- **YubiKey firmware limits are immovable**: PIV touch-cache is fixed at 15s;
  no 5-minute hardware option exists. PIN-verified state lives per *card
  session*, and card sessions die almost immediately (pcscd is
  socket-activated; the card resets on client disconnect) — so "enter PIN once
  interactively, agents ride the session" works for only seconds.
- **Topology**: user works on the devbox via **mosh** (carries no SSH
  forwards). YubiKey access from the devbox rides: laptop pcscd → SSH
  RemoteForward (loopback :12799, non-ephemeral-range on purpose — :47952 got
  squatted by an ephemeral allocation) → devbox socat bridge
  (`devbox/pcscd-bridge.service`) → user-owned socket at `~/.pcscd/pcscd.comm`
  (`PCSCLITE_CSOCK_NAME`, set in `.bashrc` keyed on the `~/.pcscd` marker dir).
  The tunnel is created on demand by the `devbox` wrapper (`scripts/devbox`) —
  it exists only while the user has connected via that wrapper. No polling
  daemons (user explicitly rejected an always-on tunnel service; it also hits
  Tailscale SSH's periodic browser re-check).
- **The recovery key must never transit the devbox** — devbox agents can read
  same-user process env/files; the recovery key would grant permanent
  human-tier access. Re-encryption involving the recovery key happens on the
  laptop only.
- **sops recipients are per-file** (hence the file-level tiers) and the repo is
  **public** (committed files are world-visible ciphertext; the fattest keys
  live in gitignored `secrets.local.env` on the devbox only).
- Both machines run unattended consumers (laptop: voxtype daemon; devbox:
  skill MCPs via `secrets KEY -- cmd`, legion/envoy infra) → each machine has
  a disk-resident **agent-tier** age key. This is deliberate and fine.
- Multiple agents share the repos' working copies; sops files must never go
  through textual merges (re-encryptions collide; resolve by picking one side
  and proving plaintext equality — precedent exists, see history).

## Current implementation (as of 2026-07-23)

Recipient roster:

| Key | Where | Decrypts |
|---|---|---|
| YubiKey PIV retired-slot 1, `age1yubikey1q266wjyhf0vfcjmy0g40m5hzadmjcqcdxdruru3gk62q39rk33xa2rd2llw` | hardware (serial 38053134); **pin-policy=never, touch-policy=cached(15s)** | everything |
| Recovery `age18fg9c68mlfmn5nz5tme0xzq4vkhx850p7293jrymhm9m8a2wxshqlmrw8h` | 1Password item "SOPS AGE Key" only | everything (break-glass; verified working) |
| laptop agent key | `~/.config/sops/age/keys.txt` (laptop) | agent tier |
| devbox agent key | `~/.config/sops/age/keys.txt` (devbox) | agent tier |

Files: `secrets.env` (committed, agent tier), `secrets.local.env` (devbox-only
gitignored, agent tier), `secrets.human.env` (committed, human tier: YubiKey +
recovery ONLY), `dojo/.env` (devbox, gitignored, agent tier — shares the same
key infrastructure; any key ceremony must include it or it breaks).

Shim (`shims/secrets`): three-file union; human tier decrypted only when a
requested key isn't in agent tier; on success caches decrypted human-tier
dotenv in `/dev/shm/.secrets-human-$UID` (mode 600) for
`SECRETS_HUMAN_CACHE_TTL` seconds (default 300); on failure in agent contexts
(detected via `OPENCODE_SESSION_ID`/`CLAUDECODE`/no-TTY) prints an AGENT
NOTICE telling the agent to ask the user rather than retry-loop.

## What was tried, in order, and why each failed

1. **pin-policy=once + touch-cached** (original): PIN prompt needs a TTY →
   agents always fail. User's manual workaround (decrypt interactively first)
   only unlocked agents for seconds — PIN state is per card session and the
   card session dies on client disconnect. Unusable.
2. **Keeping the PIN but stretching the card session** (pcscd auto-exit
   tweaks): dead on arrival — the session dies on client disconnect, not on
   an idle timer. Not pursued further.
3. **pin-policy=never + touch-cached** (current): mechanically works from all
   contexts; 15s hardware cache + 5-minute software cache. Still fails the
   workflow: a 30–90-minute DEEL session hits the touch wall repeatedly, and
   any decrypt while the user is away fails ("YubiKey not reachable").
4. **Raising the software TTL** was considered but not chosen — it's a knob,
   not a model. (It *is* the cheapest experiment: `SECRETS_HUMAN_CACHE_TTL=5400`
   ≈ a work session. The user suspects the model, not the number, is wrong.)

## Known unsolved bugs (fix or design around)

- **Something probes the human tier during opencode startup** — causes stray
  blinks/failures at load. Never traced. Suspects: a skill MCP wrapper
  requesting a key absent from agent tier, or something invoking
  `secrets list` (which probes human tier for names). Find it with an strace
  or by instrumenting `decrypt_human` to log its caller.
- `SECRETS_HUMAN_FILE` in the shim is assigned unconditionally (not
  env-overridable) — makes testing awkward.

## Design directions worth evaluating (not prescriptions)

- **Session-scoped secrets agent** (the "right shape"?): a small user daemon à
  la ssh-agent holding either the decrypted values or the unwrapped file key.
  One touch/authorization opens a *login-session-length* window; socket-based
  access; explicit `secrets lock`. The 5-min cache is a degenerate version of
  this. Could be a systemd user service on each machine; the devbox one could
  even require the laptop tunnel to be alive at unlock time only.
- **Approval-flow via envoy**: agent requests a human secret → envoy pings the
  user (Slack/desktop) → user approves → time-boxed grant. All infrastructure
  for this exists (envoy, agent notices). Matches the "human authorizes usage"
  intent far better than physical touch proximity.
- **1Password as the human tier**: `op` already guards the recovery key; the
  desktop app can prompt for authorization with its own (much longer) session
  windows; `op run`/`op read` from agents on the laptop; devbox would need a
  service-account or Connect story (evaluate exposure carefully — see the
  recovery-key-never-on-devbox constraint).
- **Re-examine tier membership**: is DEEL genuinely human-tier if the user's
  actual workflow is "agents use it constantly while I'm working"? Perhaps
  human-tier should mean "human *session* active" not "human touch per use" —
  which collapses to: unlock on `devbox` connect, lock on disconnect. The
  tunnel's existence is already a human-presence signal.
- **Per-secret TTLs / policies** instead of one cache for the whole tier.

## Verification commands

- Agent tier unattended: `ssh devbox '~/.dotfiles/shims/secrets get ANTHROPIC_API_KEY | wc -c'` (no interaction)
- Human tier: `secrets get FLEET_LICENSE_KEY` (touch on blink; instant within cache TTL)
- Hardware roundtrip: `age-plugin-yubikey --list` (laptop: direct; devbox: via tunnel, needs `PCSCLITE_CSOCK_NAME`)
- Recovery: `SOPS_AGE_KEY=$(op item get "SOPS AGE Key" --fields password --reveal | tr -d '"') sops -d secrets.human.env` (laptop only!)
- GitHub-side history: dotfiles commits `sops: touch-only yubikey identity...`,
  `yubikey: touch-to-auth + tiered sops secrets + devbox tunnel`, and this
  file's session for full context.

## Cautions for the successor

- Never run key ceremonies without including `dojo/.env`.
- Never move the recovery key through the devbox.
- Never let sops files merge textually.
- The user requires: actions proposed before taken, no unannounced
  hardware-interaction (blinks), no polling daemons, minimal commits, and no
  pushes without explicit approval. Interactive commands needing their input
  go to them to run, not fired at them with 15-second windows.
