# secretsd Install + Human-Tier Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Tasks 1–2 are AGENT-RUN. Tasks 3–5 are HUMAN-RUN on the laptop with the YubiKey attached** — an agent must not execute them (a `sops -d` of the human tier triggers a physical touch).

**Goal:** Install the `secretsd` broker via a dotfiles installer (agent), then run the one-time laptop ceremony that splits `secrets.human.env` into per-key `secrets.human.d/<KEY>.env` files, verified before the old file is removed.

**Architecture:** Part 1 mirrors `installers/voxtype.sh`: a Rust binary lands in the gitignored `bin/` with a pinned-tag marker for idempotent re-runs, systemd user units are linked and the socket-activated `.socket` is enabled, and the OpenCode plugin is registered in `opencode.json`. Part 2 is a human ceremony: one YubiKey touch decrypts the human tier, each key is re-encrypted to YubiKey+recovery only, `scripts/verify-sops-recipients` proves the recipient policy without decrypting, and only after end-to-end grant flow works is `secrets.human.env` deleted.

**Tech Stack:** bash + `set -euo pipefail`, `lib.sh` helpers (`ensure_link`, `ensure_json`), `gh`/`curl`/`tar`, `install`, `systemctl --user`, `sops` + `age` (age-plugin-yubikey), `jq`, `yq`, `jj` (colocated git), `shellcheck` (via `mise exec`).

## Global Constraints

- **Version control is `jj`, never `git`.** Commit with `jj describe -m`; push per the dotfiles convention (direct to `main`, no PR).
- **ONE commit for the installer deliverable.** Task 1 leaves the working copy uncommitted; Task 2 makes the single commit covering both `installers/secretsd.sh` and `install.sh`. Do **not** commit per task. (This overrides the writing-plans frequent-commit guidance.) The human ceremony (Tasks 3–5) is a **separate** single commit made by the human on the laptop.
- **The agent tier must keep working with zero interaction throughout.** `secrets.env` (committed) and `secrets.local.env` (devbox, gitignored) and their consumers — voxtype, legion, envoy, skill MCPs, `dojo/.env` — must never require a touch. A regression here is the worst outcome; Task 4 verifies it explicitly and it must pass before Task 5.
- **sops files are never merged textually** (existing repo rule). Per-key files also shrink the conflict surface. Never hand-merge `secrets.env`, `secrets.human.env`, or `secrets.human.d/*.env`.
- **The recovery key never transits the devbox.** Its private half lives only in 1Password; break-glass decrypts happen on the laptop only.
- **`installers/secretsd.sh` must be `shellcheck` clean.**
- **Human-tier keys are exactly:** `DEEL_API_KEY`, `PULUMI_CONFIG_PASSPHRASE`.
- **`.sops.yaml` and `installers/sops.sh` are already landed (commit b8542f15).** The recipient verifier must remain touchless and check both agent-tier files (`secrets.env` and, when present, `secrets.local.env`); do not change these files during the human ceremony.

---

## Prerequisites (cross-plan ordering)

These are owned by **separate plans**; they are called out only where this plan depends on them.

- **P1 — `secretsd` binary exists.** No release is cut yet. On these dev machines the pragmatic path is a local build: `(cd ~/Code/secretsd && cargo build --release --locked)`. Task 2 does this. **`secretsd` is not installed until either a local source build or a real release asset exists.**
- **P2 — `shims/secrets` rewrite landed** (design.md Migration step 4): human keys route to the broker over the socket and read `secrets.human.d/*.env` filenames. **HARD ORDERING — do not deploy this rewritten shim until Task 3 has created all two `secrets.human.d/*.env` files and `scripts/verify-sops-recipients` returned `rc=0`.** It is then required before Task 4 and Task 5. Deploying it first makes every human key invisible while it still lives only in `secrets.human.env`; Task 5 may remove the old file only after the rewritten shim passes Task 4.
- **P3 — secretsd OpenCode plugin landed** at `opencode/plugins/secretsd.ts` (design.md Components §2): registers the session token the shim sends. **Required before Task 4.** The installer safely skips its registration until the file exists, so P3 has a mandatory post-landing installer rerun on both machines below.

### P3 post-landing registration (AGENT-RUN on both machines)

After `opencode/plugins/secretsd.ts` lands, and before Task 4, run on **both** laptop and devbox:

```bash
cd ~/.dotfiles
bash installers/secretsd.sh
jq -e '.plugin | index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts")' opencode/opencode.json
```

Expected: the final command prints a non-negative array index and exits `0`. A missing plugin entry is a hard blocker for Task 4; do not assume the earlier pre-plugin installer run registered it.

## File Structure

| Path | Owner | Action | Responsibility |
|------|-------|--------|----------------|
| `installers/secretsd.sh` | **agent** (Task 1) | Create | Install binary → `bin/secretsd` (local build wins, else release), link+enable `secretsd.socket`, register plugin, devbox `PCSCLITE_CSOCK_NAME` import. |
| `install.sh` | **agent** (Task 2) | Modify (after line 14) | Source `installers/secretsd.sh` in dependency order. |
| `opencode/opencode.json` | **agent** (Task 1, at install time) | Modify via `ensure_json` (gated on P3) | Add the `secretsd.ts` plugin entry to `.plugin`. |
| `bin/secretsd`, `bin/.secretsd-tag`, `bin/secretsd-units/` | agent | Created at runtime | Binary, pinned-tag marker, release-bundled units. **gitignored — never in `jj`.** |
| `~/.config/systemd/user/secretsd.{service,socket}` | agent | Symlinks | User units (outside the repo). |
| `secrets.human.d/DEEL_API_KEY.env` | **human** (Task 3) | Create | Per-key sops file, YubiKey+recovery only. |
| `secrets.human.d/PULUMI_CONFIG_PASSPHRASE.env` | **human** (Task 3) | Create | Per-key sops file. |
| `secrets.human.env` | **human** (Task 5) | Delete (last) | Removed only after Tasks 3–4 pass. Recoverable via `jj`. |

---

### Task 1: Create `installers/secretsd.sh` (AGENT-RUN)

**Files:**
- Create: `installers/secretsd.sh`
- Reads at runtime: `~/Code/secretsd/target/release/secretsd`, `~/Code/secretsd/systemd/*`, release tarball, `opencode/opencode.json`

**Interfaces:**
- Consumes: `lib.sh` helpers `ensure_link`, `ensure_json`, and the exported `DOTFILES_DIR`.
- Produces (contract with P3): registers the exact plugin string `file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts`. The plugin plan must create that filename; a mismatch means the installer silently never registers.
- Release fallback accepts only `secretsd-${TAG}-linux-x86_64.tar.gz` containing the executable `secretsd` and `systemd/secretsd.service` plus `systemd/secretsd.socket`. A GitHub tag or empty release is not installable; a missing asset must warn and return success without replacing an existing install.

- [ ] **Step 1: Write the installer**

Create `installers/secretsd.sh`:

```bash
#!/bin/bash
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --- secretsd: session secrets broker (github.com/sjawhar/secretsd) ----------
# Mirrors installers/voxtype.sh: a Rust binary lands in bin/ behind a pinned-tag
# marker for idempotent re-runs; systemd user units are linked + enabled.
# Deliberate divergences from voxtype:
#   * a local dev build (~/Code/secretsd) is PREFERRED when newer — both of
#     Sami's machines carry the source and no release is cut yet;
#   * the socket-activated *.socket* is enabled (enabling the .service is wrong);
#   * the OpenCode plugin is registered in opencode.json via ensure_json.
SECRETSD_REPO="sjawhar/secretsd"
SECRETSD_SRC="${HOME}/Code/secretsd"
SECRETSD_BIN="${DOTFILES_DIR}/bin/secretsd"
SECRETSD_TAG_FILE="${DOTFILES_DIR}/bin/.secretsd-tag"
SECRETSD_RELEASE_UNITS="${DOTFILES_DIR}/bin/secretsd-units"
SECRETSD_PLUGIN="file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts"
OPENCODE_JSON="${DOTFILES_DIR}/opencode/opencode.json"

# --- 1. Resolve the binary: a fresh local build wins, else the newest release -
LOCAL_BIN="${SECRETSD_SRC}/target/release/secretsd"
if [ -x "$LOCAL_BIN" ] && { [ ! -x "$SECRETSD_BIN" ] || [ "$LOCAL_BIN" -nt "$SECRETSD_BIN" ]; }; then
    echo "Installing secretsd from local build (${LOCAL_BIN})..."
    install -m 0755 "$LOCAL_BIN" "$SECRETSD_BIN"
    echo "local-build" > "$SECRETSD_TAG_FILE"
else
    SECRETSD_TAG=$(gh release list --repo "$SECRETSD_REPO" --limit 1 --json tagName -q '.[0].tagName' 2>/dev/null || true)
    if [ -z "$SECRETSD_TAG" ]; then
        if [ ! -x "$SECRETSD_BIN" ]; then
            echo "WARNING: no secretsd release on ${SECRETSD_REPO} and no local build at ${LOCAL_BIN}."
            echo "  Build it:  (cd ${SECRETSD_SRC} && cargo build --release --locked)"
            echo "  secretsd is not installed until either a local source build or a real release asset exists."
            echo "  Skipping systemd + plugin wiring until then."
        fi
        # A previously-installed binary is fine: stay quiet and idempotent.
    elif [ ! -x "$SECRETSD_BIN" ] || [ ! -f "$SECRETSD_TAG_FILE" ] || [ "$(cat "$SECRETSD_TAG_FILE")" != "$SECRETSD_TAG" ]; then
        echo "Installing secretsd ${SECRETSD_TAG} from ${SECRETSD_REPO}..."
        tmp="$(mktemp -d)"
        url="https://github.com/${SECRETSD_REPO}/releases/download/${SECRETSD_TAG}/secretsd-${SECRETSD_TAG}-linux-x86_64.tar.gz"
        if curl -fSL "$url" -o "$tmp/secretsd.tar.gz" && \
            tar xzf "$tmp/secretsd.tar.gz" -C "$tmp" --strip-components=1; then
            install -m 0755 "$tmp/secretsd" "$SECRETSD_BIN"
            mkdir -p "$SECRETSD_RELEASE_UNITS"
            install -m 0644 "$tmp/systemd/secretsd.service" "$tmp/systemd/secretsd.socket" "$SECRETSD_RELEASE_UNITS/"
            echo "$SECRETSD_TAG" > "$SECRETSD_TAG_FILE"
        else
            echo "WARNING: release ${SECRETSD_TAG} lacks the expected secretsd-${SECRETSD_TAG}-linux-x86_64.tar.gz asset."
            echo "  secretsd is not installed until either a local source build or a real release asset exists."
            echo "  Keeping any existing install unchanged; skipping this release update."
        fi
        rm -rf "$tmp"
    fi
fi

# --- 2. Systemd user units: the repo checkout wins (edits are live on a dev
#        machine); otherwise the copy bundled in the release tarball. ----------
if [ -d "${SECRETSD_SRC}/systemd" ]; then
    UNIT_SRC="${SECRETSD_SRC}/systemd"
elif [ -d "$SECRETSD_RELEASE_UNITS" ]; then
    UNIT_SRC="$SECRETSD_RELEASE_UNITS"
else
    UNIT_SRC=""
fi

if [ -x "$SECRETSD_BIN" ] && [ -n "$UNIT_SRC" ]; then
    # The units hardcode %h/.dotfiles/... (ExecStart + SECRETSD_HUMAN_DIR).
    if [ "$DOTFILES_DIR" != "${HOME}/.dotfiles" ]; then
        echo "WARNING: DOTFILES_DIR=${DOTFILES_DIR} but the units expect %h/.dotfiles;"
        echo "  secretsd would read SECRETSD_HUMAN_DIR from the wrong path. Fix the checkout location."
    fi
    mkdir -p ~/.config/systemd/user
    ensure_link "${UNIT_SRC}/secretsd.service" ~/.config/systemd/user/secretsd.service
    ensure_link "${UNIT_SRC}/secretsd.socket"  ~/.config/systemd/user/secretsd.socket
    systemctl --user daemon-reload 2>/dev/null || true
    # Socket-activated: enable the .socket so the listener is up; the daemon
    # starts on first connect. --now starts the socket, not the daemon.
    systemctl --user enable --now secretsd.socket 2>/dev/null \
        || echo "NOTE: could not enable secretsd.socket (no user systemd session here?) — enable it on the target machine."

    # Devbox only: the daemon reads PCSCLITE_CSOCK_NAME from ITS OWN env to reach
    # the YubiKey over the on-demand pcscd tunnel. systemd user units do not
    # inherit the shell env, so import it when present. Absent = laptop (direct
    # YubiKey) or tunnel down (daemon fails fast with a clear message, by design).
    if [ -n "${PCSCLITE_CSOCK_NAME:-}" ]; then
        systemctl --user import-environment PCSCLITE_CSOCK_NAME 2>/dev/null || true
        # A running service keeps its old manager environment and old binary.
        # Restarting it applies the import and deliberately clears memory-only grants.
        systemctl --user try-restart secretsd.service 2>/dev/null || true
        systemctl --user restart secretsd.socket 2>/dev/null || true
        echo "secretsd: imported PCSCLITE_CSOCK_NAME; restarted service grants are cleared (devbox pcscd tunnel)."
    fi
else
    echo "secretsd: binary or units unavailable — skipping systemd wiring (install stays non-fatal)."
fi

# --- 3. Register the OpenCode plugin (idempotent). Gated on the plugin file so
#        this installer is safe to run before P3 lands it; rerun this installer
#        after P3 lands to register the plugin. ---------------------------------
if [ -f "${DOTFILES_DIR}/opencode/plugins/secretsd.ts" ]; then
    ensure_json "$OPENCODE_JSON" \
        "$(printf '.plugin | index("%s")' "$SECRETSD_PLUGIN")" \
        "$(printf '.plugin += ["%s"]' "$SECRETSD_PLUGIN")" \
        "Registering secretsd OpenCode plugin in opencode.json..."
else
    echo "NOTE: opencode/plugins/secretsd.ts not present yet — skipping plugin registration."
    echo "  Rerun bash installers/secretsd.sh after the secretsd OpenCode-plugin plan (P3) lands that file."
fi
```

- [ ] **Step 2: Verify the syntax parses**

Run: `bash -n installers/secretsd.sh`
Expected: no output, exit 0.

- [ ] **Step 3: Verify it is shellcheck clean**

Run: `mise exec shellcheck -- shellcheck -x --source-path=SCRIPTDIR installers/secretsd.sh; echo "rc=$?"`
Expected:
```
rc=0
```
(no findings above the default severity; `-x --source-path=SCRIPTDIR` lets shellcheck follow the `# shellcheck source=lib.sh` directive to `installers/lib.sh`.)

- [ ] **Step 4: Confirm the plugin-registration gate is inert today**

P3 has not landed `opencode/plugins/secretsd.ts` yet, so registration must self-skip and leave `opencode.json` untouched.

Run: `test -f opencode/plugins/secretsd.ts && echo "present" || echo "absent (registration will skip)"`
Expected:
```
absent (registration will skip)
```

Do **not** commit here — the single commit happens in Task 2.

---

### Task 2: Wire into `install.sh` and verify the install (AGENT-RUN, single commit)

**Files:**
- Modify: `install.sh` (insert one line after line 14, `source ".../opencode.sh"`)
- Runtime: builds `~/Code/secretsd` and runs the installer

**Interfaces:**
- Consumes: `installers/secretsd.sh` from Task 1.

- [ ] **Step 1: Add the source line in dependency order**

`secretsd.sh` needs `gh`/`jq` (mise), the sops context, and it patches `opencode.json`, so it sources **after** `opencode.sh`. Edit `install.sh` so lines 14–15 read:

```bash
source "${DOTFILES_DIR}/installers/opencode.sh"
source "${DOTFILES_DIR}/installers/secretsd.sh"
```

- [ ] **Step 2: Provide a binary (P1) so the installer has something to install**

Run: `(cd ~/Code/secretsd && cargo build --release --locked) && ls -l ~/Code/secretsd/target/release/secretsd`
Expected (final line): `-rwxr-xr-x ... /home/sami/Code/secretsd/target/release/secretsd`

- [ ] **Step 3: Run the installer (first run installs from the local build)**

Run: `bash installers/secretsd.sh`
Expected (order may vary slightly):
```
Installing secretsd from local build (/home/sami/Code/secretsd/target/release/secretsd)...
NOTE: opencode/plugins/secretsd.ts not present yet — skipping plugin registration.
  Rerun bash installers/secretsd.sh after the secretsd OpenCode-plugin plan (P3) lands that file.
```

- [ ] **Step 4: Verify the artifacts**

Run:
```bash
ls -l bin/secretsd && cat bin/.secretsd-tag && \
readlink ~/.config/systemd/user/secretsd.socket && \
systemctl --user is-enabled secretsd.socket
```
Expected:
```
-rwxr-xr-x 1 sami sami <size> <date> bin/secretsd
local-build
/home/sami/Code/secretsd/systemd/secretsd.socket
enabled
```
(If this machine has no user systemd session, the last two lines are absent and Step 3 printed the `NOTE: could not enable ...` line instead — that is the accepted non-fatal degrade.)

- [ ] **Step 5: Verify idempotency (second run is a quiet no-op)**

Run: `bash installers/secretsd.sh && echo "second-run rc=$?"`
Expected: no `Installing ...` line (the installed binary is not older than the build), only the two-line plugin-skip `NOTE`, then:
```
second-run rc=0
```

- [ ] **Step 6: Verify the working copy is exactly the two intended files**

Run: `jj diff --stat`
Expected: only `install.sh` and `installers/secretsd.sh` (the `bin/` artifacts are gitignored; `opencode.json` is unchanged because P3 has not landed):
```
install.sh              | 1 +
installers/secretsd.sh  | <N> ++++++...
2 files changed, <N> insertions(+), 0 deletions(-)
```

- [ ] **Step 7: Make the single installer commit and push**

```bash
jj describe -m "feat: add secretsd installer (binary, systemd socket, opencode plugin)"
jj bookmark list      # confirm the main bookmark position before moving it
jj tug                # move the closest bookmark (main) to @
jj git push
```
Expected: `jj git push` reports the branch updated on the remote (e.g. `Changes to push to origin: ... main@origin`), no errors.

---

### Task 3: Split the human tier into per-key files (HUMAN-RUN, laptop only)

> **STOP — human only.** Run these on the **laptop with the YubiKey attached**. An agent must not run any `sops -d` on the human tier. **Touch budget for this whole task: exactly ONE blink** (the single decrypt in Step 2). Any additional blink is a bug in this system — stop and investigate.

**Files:**
- Create: `secrets.human.d/DEEL_API_KEY.env`, `secrets.human.d/PULUMI_CONFIG_PASSPHRASE.env`
- Read: `secrets.human.env` (decrypted once), `.sops.yaml` (recipient source of truth)

- [ ] **Step 1: Confirm prerequisites and starting state (no touch)**

Run:
```bash
cd ~/.dotfiles
test -x bin/secretsd && echo "binary ok"
test -f secrets.human.env && echo "source present"
test ! -d secrets.human.d && echo "no human.d yet"
```
Expected:
```
binary ok
source present
no human.d yet
```

- [ ] **Step 2: Split with a single decrypt (EXACTLY ONE touch)**

> The decrypt below is the **only** YubiKey blink. Encryption uses public age keys and never touches the hardware. Plaintext is held only in the shell variable and an encrypted stdin pipe: it is **never written to disk**. Each temporary file contains ciphertext only. `--filename-override` makes sops select the `secrets\.human\.d/[^/]+\.env$` rule (YubiKey+recovery) even though its input is `/dev/stdin`; omitting it would select the default rule and leak the key to the agent recipient.

```bash
cd ~/.dotfiles
set -euo pipefail
mkdir -p secrets.human.d
umask 077
tmp=""
trap 'rm -f "$tmp"' EXIT

# ONE decrypt of the whole human tier -> EXACTLY ONE YubiKey blink here.
# Captured to a shell variable; plaintext is never written to disk.
plain="$(sops -d --output-type dotenv secrets.human.env)"   # <-- touch #1 (the only touch)

for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
    dest="secrets.human.d/${key}.env"
    tmp="$(mktemp "${dest}.tmp.XXXXXX")"
    printf '%s\n' "$plain" | grep -E "^${key}=" | \
        sops --filename-override "$dest" --input-type dotenv --output-type dotenv -e /dev/stdin > "$tmp"
    mv "$tmp" "$dest"                                      # ciphertext-only temporary file -> destination
    tmp=""
done
unset plain
trap - EXIT
```
Expected: no error output; the loop completes silently and returns to the prompt with no further blink.

- [ ] **Step 3: Structural check — one assignment per file, name == filename (no touch)**

Reads plaintext key names from sops metadata only; never decrypts.

```bash
for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
    f="secrets.human.d/${key}.env"
    names="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f" | grep -v '^sops_' | cut -d= -f1)"
    count="$(printf '%s\n' "$names" | grep -c .)"
    if [ "$count" = "1" ] && [ "$names" = "$key" ]; then
        echo "OK   $f -> $names"
    else
        echo "FAIL $f: expected one assignment named $key, got: ${names:-(none)}"
    fi
done
```
Expected:
```
OK   secrets.human.d/DEEL_API_KEY.env -> DEEL_API_KEY
OK   secrets.human.d/PULUMI_CONFIG_PASSPHRASE.env -> PULUMI_CONFIG_PASSPHRASE
```
If any line says `FAIL`, run the rollback in Task 5 → "Before removal" and redo Step 2.

- [ ] **Step 4: Prove the recipient policy with `verify-sops-recipients` (no touch)**

This proves each file is encrypted to YubiKey+recovery only, to **no** agent key, and that no `human.d` key name collides with either agent-tier file: `secrets.env` or (when it exists) `secrets.local.env`. It never runs `sops -d`.

Run: `DOTFILES_DIR="$PWD" scripts/verify-sops-recipients; echo "rc=$?"`
Expected (recipients shown in `sort`ed order; fingerprints match `.sops.yaml`):
```
== recipient roster (from .sops.yaml) ==
  human tier : age18fg9c68mlfmn5nz5tme0xzq4vkhx850p7293jrymhm9m8a2wxshqlmrw8h age1yubikey1q266wjyhf0vfcjmy0g40m5hzadmjcqcdxdruru3gk62q39rk33xa2rd2llw
  agent keys : age1jf63nvl6uenkwalyysa5hqv0k7ks5k6mjggqxlj7fwwp0gnkfvxszyxd2q age1qz0v8s5fnwvq9g4u49jkl8jdzejmssxxh6rw3cz0yxa370c05ynsak3gkk

== (a) secrets.human.d/*.env recipients ==
  PASS  secrets.human.d/DEEL_API_KEY.env -> age18fg9c68mlfmn5nz5tme0xzq4vkhx850p7293jrymhm9m8a2wxshqlmrw8h age1yubikey1q266wjyhf0vfcjmy0g40m5hzadmjcqcdxdruru3gk62q39rk33xa2rd2llw
  PASS  secrets.human.d/PULUMI_CONFIG_PASSPHRASE.env -> age18fg9c68mlfmn5nz5tme0xzq4vkhx850p7293jrymhm9m8a2wxshqlmrw8h age1yubikey1q266wjyhf0vfcjmy0g40m5hzadmjcqcdxdruru3gk62q39rk33xa2rd2llw

== (b) human.d key names vs agent tier (secrets.env + secrets.local.env if present) ==
  PASS  no human.d key name also appears in either agent-tier file.

verify-sops-recipients: OK
rc=0
```
**Do not proceed to Task 5 unless `rc=0`.** A `FAIL` in section (a) means a file is readable by the agent key (human gate defeated); a `FAIL` in section (b) identifies a duplicate in `secrets.env` or `secrets.local.env` that would let a consumer read the agent-tier copy and bypass the gate. Either way: rollback (Task 5 → "Before removal") and redo Step 2.

`secrets.human.env` is still present and unchanged at the end of this task — nothing destructive has happened yet.

---

### Task 4: End-to-end verification (HUMAN-RUN, laptop) — no-regression gate

> **STOP — human only.** Requires Task 3 completed, then **P2 (shim rewrite) and P3 (plugin) landed, P3 post-landing registration completed, and the secretsd installer run on both machines.** The Task 3 split and recipient verification are a hard prerequisite for deploying P2. Each expected touch is annotated `(touch)`; any blink not annotated is a bug — stop.

**Files:** none created/modified. This task is pure verification and gates Task 5.


- [ ] **Step 1: Agent tier unattended on laptop and devbox — the critical no-regression check (NO touch)**

These commands exercise both agent-tier files on both machines. They must involve no broker and no hardware. On each machine, `secrets.local.env` is decrypted only if it exists.

On the laptop, run:
```bash
cd ~/.dotfiles
~/.dotfiles/shims/secrets get ANTHROPIC_API_KEY | wc -c
sops -d --output-type dotenv secrets.env >/dev/null
if [ -f secrets.local.env ]; then sops -d --output-type dotenv secrets.local.env >/dev/null; fi
```

From the laptop, run the corresponding devbox checks:
```bash
ssh devbox 'set -euo pipefail
cd ~/.dotfiles
~/.dotfiles/shims/secrets get ANTHROPIC_API_KEY | wc -c
sops -d --output-type dotenv secrets.env >/dev/null
if [ -f secrets.local.env ]; then sops -d --output-type dotenv secrets.local.env >/dev/null; fi'
```

Expected: each command exits `0`; each `secrets get` prints a positive byte count (for example, `109`); every applicable `sops -d` succeeds; and there are **zero YubiKey touches**. If any command blinks, fails, or hangs, STOP — unattended agent-tier decryption regressed; do not touch Task 5.

- [ ] **Step 2: Grant every human-tier key through the real shim/broker path (3 announced touches)**

In one fresh OpenCode session on the laptop, run:
```bash
for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
    printf '== %s ==\n' "$key"
    ~/.dotfiles/shims/secrets get "$key" | wc -c
```

Expected: each key prints a positive byte count. For **each** first grant, the broker emits an AGENT NOTICE / `notify-send` announcement naming that key and a request ID **before exactly one** YubiKey blink `(touch)`. There are exactly two announced touches total — one each for `DEEL_API_KEY` and `PULUMI_CONFIG_PASSPHRASE` — and **no unannounced blink**. A failure, zero-byte value, missing announcement, extra touch, or unannounced blink blocks Task 5.

- [ ] **Step 3: Repeat every granted key in the same session (NO touch)**

Run again in that same OpenCode session:
```bash
for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
    ~/.dotfiles/shims/secrets get "$key" | wc -c
```

Expected: three positive byte counts print immediately, with **no announcement and no blink**. This proves the broker's memory-only grant is scoped to the session after each first grant.

- [ ] **Step 4: A new session needs a fresh grant (1 announced touch)**

In a **second** OpenCode session, run: `~/.dotfiles/shims/secrets get PULUMI_CONFIG_PASSPHRASE | wc -c`
Expected: a positive byte count after a new announcement and exactly one YubiKey blink `(touch)`. No value from the first session is reused; grants are per session token.

- [ ] **Step 5: Devbox tunnel up — grant announces on laptop and completes (1 announced touch)**

With the devbox PC/SC tunnel **up**, from the laptop run:
```bash
ssh devbox 'timeout 120 ~/.dotfiles/shims/secrets get PULUMI_CONFIG_PASSPHRASE | wc -c'
```

Expected: the laptop receives an announcement naming `PULUMI_CONFIG_PASSPHRASE`, then exactly one YubiKey blink `(touch)` completes the request; the devbox prints a positive byte count before the 120-second timeout. There is no unannounced blink.

- [ ] **Step 6: Devbox tunnel down — fail fast, never hang (NO touch)**

With the devbox PC/SC tunnel **down**, from the laptop run:
```bash
timeout 5 ssh devbox 'timeout 5 ~/.dotfiles/shims/secrets get PULUMI_CONFIG_PASSPHRASE'
```

Expected: non-zero exit within five seconds, a clear `YubiKey unreachable` message (including the devbox-wrapper guidance), **no hang, and no touch**. A timeout with no clear message is a failure; do not proceed to Task 5.

- [ ] **Step 7: Cross-session isolation (NO touch — deny path)**

While session A (Step 2) holds its grant, in session B request the same key, then deny B's own pending request from a laptop terminal:
```bash
secrets grants          # note the pending request id for session B
secrets deny <id>       # reject B's request
```
Expected: B never receives A's value; B's request is its **own** pending entry and is denied without any blink.

- [ ] **Step 8: `secrets list` never touches the YubiKey (NO touch)**

Run: `secrets list`
Expected: agent-tier names plus the two human-tier names (`DEEL_API_KEY`, `PULUMI_CONFIG_PASSPHRASE`) marked human tier, with **zero** blink (design: strace-verifiable).

- [ ] **Step 9: Broker restart gives a clear re-approval message (1 touch)**

```bash
systemctl --user restart secretsd.socket
secrets get PULUMI_CONFIG_PASSPHRASE
```
Expected: a clear "broker restarted; re-approval required" message (no hang), then **one** blink `(touch)`, then the value. Memory-only grants are lost on restart by design.

All nine steps must pass. Step 1 is the unattended agent-tier no-regression gate; Steps 2–6 prove every human key and both devbox tunnel states before Task 5 may delete anything.

---

### Task 5: Remove `secrets.human.env` and commit (HUMAN-RUN, laptop) — with rollback

> **STOP — human only.** This is the single destructive step. Do it **only after Task 3 `rc=0`, all two individual grants in Task 4 Step 2 passed, and all of Task 4 passed**, and only with P2 (rewritten shim reading `secrets.human.d/`) landed — otherwise removing the file regresses human-tier reads.

**Files:**
- Delete: `secrets.human.env`

- [ ] **Step 1: Final guard — verifier still green and pre-ceremony revision recorded (no touch)**

Run:
```bash
cd ~/.dotfiles
pre_ceremony_change_id="$(jj log -r @ -T 'change_id')"
printf 'pre-ceremony change: %s\n' "$pre_ceremony_change_id"
DOTFILES_DIR="$PWD" scripts/verify-sops-recipients; echo "rc=$?"
```
Expected: the full verifier report has no `FAIL` lines and ends in:
```
verify-sops-recipients: OK
rc=0
```
Keep the printed pre-ceremony change ID for the immediate rollback below. A non-zero verifier exit is a hard stop; do not remove the source.

- [ ] **Step 1b: Verify per-key files hold the same values as the original (no touch)**

This gate proves the split files contain the correct secret bytes, not a malformed split that would only surface after deletion. It compares SHA256 hashes to avoid printing secrets to the terminal or transcript.

Run:

```bash
cd ~/.dotfiles
sops -d --output-type dotenv secrets.human.env > /dev/shm/.mig-check.$$
for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
  old=$(grep "^${key}=" /dev/shm/.mig-check.$$ | cut -d= -f2- | sha256sum | cut -c1-16)
  new=$(./shims/secrets get "$key" | sha256sum | cut -c1-16)
  [ "$old" = "$new" ] && echo "MATCH  $key" || echo "MISMATCH $key  old=$old new=$new"
done
shred -u /dev/shm/.mig-check.$$
```

**Why hashes:** Comparing hashes instead of values ensures no secret is printed to a terminal or a transcript, protecting the human-tier keys from accidental exposure in logs or session history.

**YubiKey touches:** The `secrets get` calls reuse existing grants from Task 4, so the only YubiKey touch is the single `secrets.human.env` decrypt. No new touches are required.

Expected: every key prints `MATCH`. A `MISMATCH` means the split files contain wrong bytes; do not proceed to Step 2 — rollback (Task 5 → "Before removal") and redo Task 3 Step 2.


- [ ] **Step 2: Remove the old single-file human tier**

Run:
```bash
cd ~/.dotfiles
rm secrets.human.env
```
Expected: no output. (`jj` auto-snapshots the deletion into the working copy.)

**Immediate rollback — deleted but not yet committed:** If anything is wrong after Step 2, restore the original ciphertext from the ID recorded in Step 1, then remove the split files to abandon the split completely:
```bash
cd ~/.dotfiles
jj restore --from <pre-ceremony-change-id> secrets.human.env
rm -rf secrets.human.d
```
Expected: `secrets.human.env` is restored into the working copy and the two new per-key ciphertext files are gone; no YubiKey touch occurs. Do not proceed to Step 3 after this rollback — correct the failure and redo Task 3.

- [ ] **Step 3: Re-confirm the full recipient verifier passes after deletion (no touch)**

Run: `DOTFILES_DIR="$PWD" scripts/verify-sops-recipients; echo "rc=$?"`
Expected:
```
== (b) human.d key names vs agent tier (secrets.env + secrets.local.env if present) ==
  PASS  no human.d key name also appears in either agent-tier file.

verify-sops-recipients: OK
rc=0
```
The **full** verifier must exit `0`; do not filter it through `grep`, which could hide a recipient failure in section (a).

- [ ] **Step 4: Confirm the working copy is the intended ceremony change (no touch)**

Run: `jj diff --stat`
Expected: two added per-key files and the deleted source, nothing else:
```
secrets.human.d/DEEL_API_KEY.env            | <N> ++++...
secrets.human.d/PULUMI_CONFIG_PASSPHRASE.env| <N> ++++...
secrets.human.env                           | <M> ----...
3 files changed, <N> insertions(+), <M> deletions(-)
```

- [ ] **Step 5: Commit and push the ceremony (single human commit)**

```bash
jj describe -m "chore(secrets): split human tier into per-key files; drop secrets.human.env"
jj tug
jj git push
```
Expected: `jj git push` reports `main@origin` updated, no errors.

**Rollback — before removal (Task 3/4 failed):** nothing is committed and `secrets.human.env` is untouched, so:
```bash
cd ~/.dotfiles && rm -rf secrets.human.d
```
This returns the working copy to its pre-ceremony state (no touch). Redo Task 3.

**Rollback — after removal/commit (regression found later):** `secrets.human.env` is recoverable from `jj` history (it was committed):
```bash
cd ~/.dotfiles
jj log -r 'ancestors(@, 5)'                          # find the change BEFORE the ceremony commit
jj restore --from <that-change> secrets.human.env    # brings the file back into @
```
Then restore the old shim behavior (revert P2) before relying on it again. This needs no YubiKey touch — it operates on ciphertext.

**Break-glass (YubiKey lost/broken):** decrypt with the 1Password recovery key **on the laptop only**:
```bash
SOPS_AGE_KEY="$(op read 'op://Private/sops-recovery-age/private-key')" \
  sops -d secrets.human.d/DEEL_API_KEY.env
```
**The recovery private key must NEVER be exported to, forwarded to, or used on the devbox.** It exists only in 1Password and on the laptop for the duration of the command.

---

## Self-Review

**Spec coverage (MUST DO items):**
- Installer fetches a versioned release asset with a pinned-tag marker, but leaves any existing install unchanged if the expected tarball is absent → Task 1 Step 1 (`gh release list`, `.secretsd-tag`, `curl -fSL … -o`, `tar`). ✓
- Prefer newer local build from `~/Code/secretsd/target/release/secretsd` → Task 1 Step 1 (`-nt` check). ✓
- `ensure_link` both units, `daemon-reload`, enable the **`.socket`** → Task 1 Step 1 (§2). ✓
- Register plugin via `ensure_json`, matching `file://{env:HOME}/…` entries → Task 1 Step 1 (§3), then explicit P3 post-landing installer rerun + `jq -e` verification on both machines. ✓
- `SECRETSD_HUMAN_DIR` handled (unit hardcodes `%h/.dotfiles/secrets.human.d`; installer warns if `DOTFILES_DIR` differs) and devbox `PCSCLITE_CSOCK_NAME` import followed by `try-restart secretsd.service` (clearing memory-only grants) → Task 1 Step 1 (§2). ✓
- Idempotent + non-fatal on missing release → Task 1 (quiet-when-binary-exists branch), verified Task 2 Steps 3/5. ✓
- Source line added in the right order → Task 2 Step 1 (after `opencode.sh`). ✓
- Split into per-key files for the two keys → Task 3 Step 2. ✓
- Touch locations/counts stated (exactly one in Task 3; each annotated in Task 4) → Task 3 header + Task 4 annotations. ✓
- Run `verify-sops-recipients` and require pass **before** removing the old file → Task 3 Step 4 gates Task 5. ✓
- Confirm no human key in either agent-tier file (`secrets.env` and conditional `secrets.local.env`) → Task 3 Step 4 §(b) + Task 5 Step 3. ✓
- Full design.md Verification checklist plus laptop/devbox unattended-decrypt and tunnel-up/down gates → Task 4 Steps 1–9. ✓
- Explicit rollback before deletion, immediately after uncommitted deletion, after commit, and 1Password break-glass never on devbox → Task 5 rollback blocks. ✓
- Global Constraints: jj-not-git, one installer commit, agent-tier zero-interaction, no textual sops merge, recovery key off devbox, shellcheck clean → Global Constraints + Task 1 Step 3 + Task 4 Step 1. ✓
- Ordering: nothing destructive before its verification → removal (Task 5) strictly after Tasks 3–4. ✓
- No invented release tag or incomplete release recipe; behavior with no release or a missing asset is explicit and non-fatal → Task 1 Step 1 warning branches. ✓

**Placeholder scan:** every code step has complete code; every command has expected output. No `TBD`/`verify it works`/bare commands. ✓

**Type/name consistency:** plugin string identical in check + transform + P3 contract (`file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts`); key list identical across Tasks 3–5 (`DEEL_API_KEY`, `PULUMI_CONFIG_PASSPHRASE`); marker file `bin/.secretsd-tag` and units dir `bin/secretsd-units` consistent across §1–§2. ✓

**Ambiguities resolved (with reasoning):**
1. **Unit source when both a local checkout and a release exist** — resolved to prefer `~/Code/secretsd/systemd` (edits are live on a dev machine, mirroring how voxtype links config from the repo) and fall back to the release-bundled `bin/secretsd-units`. Both machines carry the source, so the checkout path is the common case.
2. **No release cut yet** — rather than suggest an incomplete `gh release create`, the plan removes that recipe. The installer degrades non-fatally until a local build or a release with the required binary-and-systemd asset exists; Task 2 uses the local build. This keeps the agent tier and install unaffected.
3. **Plugin registration vs. the plugin being a separate plan** — `ensure_json` remains gated on the file so a pre-P3 run is safe, but it cannot self-register later. The explicit P3 post-landing rerun and `jq -e` check on both machines make registration a required gate before Task 4.
4. **Predictable touch count and plaintext residency during the split** — the plan uses a single in-memory decrypt (one touch), then streams each selected assignment through sops with a destination filename override into a ciphertext-only temporary file. Metadata-only structural and recipient checks need no touch, so the split is one blink and never writes plaintext at rest.
