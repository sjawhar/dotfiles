# secretsd Rust Client Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is deliberately gate-driven: do not remove a legacy path until the named gate immediately before it passes on both target machines.

**Goal:** Replace the dotfiles bash `secrets` shim with the released Rust client while preserving unattended, broker-free agent-tier access throughout the migration.

**Architecture:** `mise` owns the versioned `secretsd` release, including the Rust `secrets` CLI and release-owned OpenCode plugin. Dotfiles retain only deployment wiring: packaged user-unit links, a machine-local service drop-in with resolved helper paths, and the OpenCode plugin reference. The bash shim remains first on `PATH` during validation; the Rust binary is invoked at its release-owned absolute path until every target has passed the agent and human gates.

**Tech Stack:** mise GitHub backend; Rust `secretsd`/`secrets` release; bash + `set -euo pipefail`; `systemctl --user`; sops + age-plugin-yubikey; OpenCode; jj; ShellCheck.

## Global Constraints

- Use **jj, never git**. There is **one commit for the complete dotfiles cutover deliverable, never one per task**.
- Every recovery command uses a path-scoped restore in this exact form: `jj restore --from "$pre_cutover_revision" -- "$path"`. Never run unscoped `jj restore`; an unscoped restore destroyed migrated ciphertext earlier today.
- `shellcheck` is pinned in `mise.toml`; every changed shell file must be clean under `mise exec shellcheck -- shellcheck -x --source-path=SCRIPTDIR "$changed_shell_file"` before the single commit.
- The agent tier is an invariant: `secrets.env` and optional `secrets.local.env` must work without interaction and without a daemon at every intermediate step. This protects voxtype, legion, envoy, skill MCPs, and dojo.
- Never automate `sops -d` for any `secrets.human.d/*.env` file. Human-tier validation requires a human-present YubiKey touch; automation may inspect filenames, SOPS metadata, service state, and command exit status only.
- Do not change, delete, rename, or shadow `shims/secrets` until **Gate C — Rust agent client on every target** has passed. It is the live client on both machines.
- The target set is the laptop and `devbox`. The laptop reaches `devbox` through the existing `devbox` SSH host alias; a command run on either target must use that target's real `$HOME` paths rather than assuming `/home/sami` or `/home/ubuntu`.

## Cutover Decision: Client Coexistence

Keep `~/.dotfiles/shims` first on `PATH` until the retirement task. This matches the current `.bashrc` contract and leaves every existing consumer on the proven bash client. During the transition, invoke the Rust client explicitly as `"$(mise which secrets)"`; this selects the released executable even though `command -v secrets` continues to resolve to `~/.dotfiles/shims/secrets`. The explicit path prevents a client gap, permits direct A/B checks against one repository state, and makes removing the shim a single, reversible final switch.

---

## Task Index

Execute the gate-bound tasks in numerical order: Task 1 (release and mise pin), Task 2 (deployment wiring), Task 3 (two-machine Rust validation), Task 4 (bash-client retirement), Task 5 (plugin cleanup), then Task 6 (final acceptance and one commit). Each task below names the gate it consumes and produces; an unmet gate is a stop condition, not an invitation to reorder or bypass a deletion.

## Self-Review

**Requirement coverage:**

- mise owns `github:sjawhar/secretsd` at the concrete `v0.1.0` release pin; Task 1 states that `github:` resolves a release asset, requires the tag and five-payload artifact gate first, and makes pre-release installer execution an explicit non-fatal skip.
- Task 2 removes the fetch/local-build/copy/tag-marker/plugin-source responsibilities from `installers/secretsd.sh`, retaining only release-unit links, `daemon-reload`, socket enablement, a machine-local drop-in, and release-plugin registration. It never enables `secretsd.service`.
- The drop-in records absolute `SECRETSD_HUMAN_DIR`, real `SECRETSD_SOPS_BIN`, and a PATH containing the real `secretsd` and `age-plugin-yubikey` directories; devbox persists its absolute `PCSCLITE_CSOCK_NAME`. The systemd minimal-PATH, silent spawn-`INTERNAL`, and unusable-mise-shim failure history is recorded directly beside the implementation.
- Task 3's Gate C is mandatory on the laptop and devbox before any shim deletion: each command runs `ssh` with `secrets get ANTHROPIC_API_KEY`, an empty runtime environment, and the daemon socket stopped. Gate D is explicitly human-run and exercises touch, cache, injection, list, grants, and lock per machine.
- The coexistence decision is explicit: keep the bash shim first on PATH and invoke `"$(mise which secrets)"` for A/B validation; Task 4 changes ordinary PATH resolution only after Gates C and D. Gate E repeats the exact no-runtime SSH regression after deletion.
- Every requested deletion has a named prerequisite in Task 5's ledger: installer acquisition bulk follows Gate A; bash shim and test follow Gates C, D, and E; plugin and test follow Gates B, E, and F.
- Every destructive repository rollback uses `jj restore --from "$revision" --` followed by only named paths. The retained devbox hand-written unit and drop-in are backed up once and have a separate path-specific external rollback. No recovery path uses unscoped `jj restore`.
- The plan states jj-only version control, one final cutover commit, ShellCheck, zero-interaction agent-tier preservation, and the ban on automation against `secrets.human.d` decryption.

**Consistency check:** all release plugin references use `file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts`; all direct client resolution uses `mise which secrets`; all target agent gates use `ANTHROPIC_API_KEY`; all socket activation commands operate on `secretsd.socket`; and all human-tier checks avoid printing values.

**Placeholder and safety scan:** every operational command has an expected result; the plan contains no deferred markers, no generic verification instruction, no unscoped restore, no change to `/home/sami/Code/secretsd`, and no automated decrypt of `secrets.human.d`.

### Task 6: Run the release and dotfiles acceptance suite, then make one cutover commit

**Files:**
- Verify: `mise.toml`, `installers/secretsd.sh`, `opencode/opencode.json`
- Verify deleted: `shims/secrets`, `shims/tests/test_secrets_shim.py`, `opencode/plugins/secretsd.ts`, `opencode/plugins/secretsd.test.ts`
- Commit once: the complete dotfiles cutover deliverable

**Interfaces:**
- Consumes: Gate A through Gate F.
- Produces: one pushed jj change containing the mise pin, deployment-only installer, release plugin reference, and only the gate-authorized deletions.

- [ ] **Step 1: Run the release-owned test suite before finalizing its dotfiles consumer**

Run:

```bash
cd ~/Code/secretsd
cargo nextest run --workspace --all-targets --all-features
bun --cwd opencode run test:secretsd-plugin
```

Expected: every Rust test and every plugin test passes with `0 fail`; the combined release verification records 123 successful tests. The test output must not print a secret value or a session-token value. This is the final ownership check before the dotfiles duplicate tests are absent.

- [ ] **Step 2: Run the final dotfiles static and configuration checks**

Run:

```bash
cd ~/.dotfiles
bash -n installers/secretsd.sh
mise exec shellcheck -- shellcheck -x --source-path=SCRIPTDIR installers/secretsd.sh
jq empty opencode/opencode.json
grep -Fx '"github:sjawhar/secretsd" = "v0.1.0"  # Rust secrets CLI, daemon, generic units, and OpenCode plugin' mise.toml
test ! -e shims/secrets
test ! -e shims/tests/test_secrets_shim.py
test ! -e opencode/plugins/secretsd.ts
test ! -e opencode/plugins/secretsd.test.ts
```

Expected: the syntax, ShellCheck, JSON parse, and four absence tests are silent and exit `0`; grep prints the exact pinned mise line. No command invokes `sops -d` against `secrets.human.d`.

- [ ] **Step 3: Run the final user-observable unattended-agent acceptance check on both machines**

Run from the laptop:

```bash
check_agent_injection() {
    local host="$1"
    ssh "$host" 'set -euo pipefail
        source "$HOME/.dotfiles/.bashrc"
        sops_dir="$(dirname "$(mise which sops)")"
        env -i \
            HOME="$HOME" \
            DOTFILES_DIR="$HOME/.dotfiles" \
            PATH="$HOME/.local/bin:${sops_dir}:/usr/local/bin:/usr/bin" \
            secrets ANTHROPIC_API_KEY -- env | grep -q "^ANTHROPIC_API_KEY="'
}
check_agent_injection "$(hostname -f)"
check_agent_injection devbox
```

Expected: no output and exit `0` twice. The command verifies the drop-in-compatible Rust client can directly decrypt the unattended agent tier and inject it only into a child process without printing it. It is the user-observable acceptance check for the invariant relied on by voxtype, legion, envoy, skill MCPs, and dojo: no YubiKey touch, no human-tier decrypt, no token, and no daemon dependency.

- [ ] **Step 4: Inspect the scoped cutover diff and make exactly one jj commit**

Run:

```bash
cd ~/.dotfiles
jj diff --git -- mise.toml installers/secretsd.sh opencode/opencode.json shims/secrets shims/tests/test_secrets_shim.py opencode/plugins/secretsd.ts opencode/plugins/secretsd.test.ts
jj describe -m "chore(secrets): cut over to released Rust client"
jj bookmark list
jj tug
jj git push
```

Expected: the diff contains exactly the mise pin, deployment-only installer, release-owned plugin reference, and four gate-authorized deletions; it contains no secret ciphertext or unrelated working-copy path. `jj git push` updates the tracked main bookmark with one cutover commit and no errors. Do not create task commits, use git, absorb unrelated work, or use a broad restore. If the scoped diff reveals an unrelated path, stop before `jj describe`; keep it intact and move the cutover to a clean jj working change rather than including or reverting somebody else's work.

**Rollback after the commit:** The whole release cutover is recoverable, but recover the smallest failed surface first. For an agent-client regression, restore only the two shim paths with Task 4's command. For a plugin regression, restore only the two plugin paths with Task 5's command. For installer or configuration regression, run:

```bash
cd ~/.dotfiles
jj restore --from "$pre_cutover_revision" -- mise.toml installers/secretsd.sh opencode/opencode.json
```

Expected: only the named installation/configuration paths return to their pre-cutover contents; shims, plugin copies, human ciphertext, and any unrelated working change remain unchanged. Re-run the relevant named gate before attempting the final commit again.

---

### Task 5: Remove the duplicate OpenCode plugin only after release ownership is proven

**Files:**
- Delete: `opencode/plugins/secretsd.ts`
- Delete: `opencode/plugins/secretsd.test.ts`

**Interfaces:**
- Consumes: Gate B, Gate E, the installed release plugin, and the `secretsd` repository's plugin test command.
- Produces: one authoritative plugin source and test suite in `secretsd`; dotfiles loads it from the stable installed path.
- Gate F — **Release plugin ownership:** both target configurations reference only `file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts`, the installed plugin exists, and the secretsd-owned plugin test suite has zero failures. The local duplicate plugin and test cannot be deleted until Gate F passes.

**Deletion-gate ledger:**

| Legacy material | Required gate before removal | Where removal occurs |
| --- | --- | --- |
| Release fetching, local Cargo-build preference, binary copies, release-unit copies, tag marker, manager-environment import, and old dotfiles-plugin registration from `installers/secretsd.sh` | Gate A — release artifact is installable | Task 2 Step 2 |
| `shims/secrets` and `shims/tests/test_secrets_shim.py` | Gate C, Gate D, and Gate E | Task 4 Step 2 |
| `opencode/plugins/secretsd.ts` and `opencode/plugins/secretsd.test.ts` | Gate B, Gate E, and Gate F | This task |

The Task 2 installer reduction is safe at Gate A because it removes only alternate acquisition paths; the live bash shim remains first on PATH until Gate E. The later deletes remove active fallbacks, so their stronger target-machine gates are mandatory.

- [ ] **Step 1: Establish Gate F without reading a human ciphertext file**

Run on the laptop:

```bash
release_plugin="$HOME/.local/share/secretsd/opencode/plugins/secretsd.ts"
test -f "$release_plugin"
jq -e '.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)' ~/.dotfiles/opencode/opencode.json
bun --cwd "$HOME/Code/secretsd/opencode" run test:secretsd-plugin
```

Expected: the file test is silent and exits `0`; jq prints `true`; and the secretsd-owned plugin suite ends with `0 fail`. It exercises the relocated source and tests rather than the soon-to-be-deleted dotfiles copies, and it never invokes `sops`.

Run the target-installation portion on devbox:

```bash
ssh devbox 'test -f "$HOME/.local/share/secretsd/opencode/plugins/secretsd.ts" && jq -e '\''.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)'\'' "$HOME/.dotfiles/opencode/opencode.json"'
```

Expected: `true` is printed and the command exits `0`. If either target is missing the installed plugin or still references the dotfiles source, do not delete anything; rerun Task 2 after fixing the release installation.

- [ ] **Step 2: Delete only the duplicate source and test after Gate F passes**

Run:

```bash
cd ~/.dotfiles
pre_plugin_cleanup_revision="$(jj log -r @ -T 'change_id ++ "\n"')"
printf 'pre-plugin-cleanup revision: %s\n' "$pre_plugin_cleanup_revision"
rm -- opencode/plugins/secretsd.ts opencode/plugins/secretsd.test.ts
test ! -e opencode/plugins/secretsd.ts
test ! -e opencode/plugins/secretsd.test.ts
jq -e '.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)' opencode/opencode.json
```

Expected: the revision identifier is printed; both removal checks are silent; jq prints `true`; and every command exits `0`. Restart OpenCode after this deletion; expected behavior is normal startup with the release-owned plugin loaded from the unchanged configuration path.

**Rollback:** If OpenCode cannot load the installed plugin after deletion, restore only the duplicate source and test while retaining the installed-path configuration for diagnosis:

```bash
cd ~/.dotfiles
jj restore --from "$pre_plugin_cleanup_revision" -- opencode/plugins/secretsd.ts opencode/plugins/secretsd.test.ts
test -f opencode/plugins/secretsd.ts
test -f opencode/plugins/secretsd.test.ts
```

Expected: the two named paths return, no configuration, shim, installer, or secret ciphertext changes, and the old dotfiles plugin remains available solely until Gate F can be repaired. Do not use unscoped `jj restore`.

---

### Task 4: Retire the bash client only after both target-machine gates

**Files:**
- Delete: `shims/secrets`
- Delete: `shims/tests/test_secrets_shim.py`

**Interfaces:**
- Consumes: Gate C and Gate D passed on the laptop and devbox, plus the release-owned `secrets` executable already available at `~/.local/bin/secrets`.
- Produces: a PATH that resolves `secrets` to the Rust release instead of the dotfiles shim.
- Gate E — **Post-removal agent continuity:** the Rust `secrets` command passes the same two non-interactive, no-`XDG_RUNTIME_DIR`, daemon-stopped SSH checks after the deletion. Task 5 is blocked until Gate E passes.

- [ ] **Step 1: Reconfirm the deletion authorization**

Before removing either file, require the recorded evidence from Task 3:

```text
Gate C — Rust agent client on every target: PASS on laptop and devbox
Gate D — Rust human client on every target: PASS on laptop and devbox, human-performed
```

Expected: all four named results are present in the cutover record. A missing, failed, or unrecorded result is a hard stop: leave both bash files in place and rerun the failed gate. The old shim is never a fallback after deletion; restore it instead of inventing a second compatibility path.

- [ ] **Step 2: Delete the shim and its focused test in one reversible working-copy change**

Run:

```bash
cd ~/.dotfiles
pre_shim_removal_revision="$(jj log -r @ -T 'change_id ++ "\n"')"
printf 'pre-shim-removal revision: %s\n' "$pre_shim_removal_revision"
rm -- shims/secrets shims/tests/test_secrets_shim.py
test ! -e shims/secrets
test ! -e shims/tests/test_secrets_shim.py
```

Expected: the revision identifier is printed; `rm` and both absence tests produce no further output and exit `0`. The `shims/` directory remains on PATH but has no `secrets` entry, so the release-owned executable is the next eligible command. No human-tier ciphertext is read or decrypted.

- [ ] **Step 3: Prove PATH resolution and the critical agent workflow after deletion**

Run from the laptop:

```bash
source ~/.dotfiles/.bashrc
resolved_secrets="$(command -v secrets)"
printf 'resolved=%s\n' "$resolved_secrets"
test "$resolved_secrets" = "$HOME/.local/bin/secrets"

check_post_removal_agent() {
    local host="$1"
    ssh "$host" 'set -euo pipefail
        source "$HOME/.dotfiles/.bashrc"
        sops_dir="$(dirname "$(mise which sops)")"
        systemctl --user stop secretsd.service secretsd.socket
        trap "systemctl --user start secretsd.socket" EXIT
        env -i \
            HOME="$HOME" \
            DOTFILES_DIR="$HOME/.dotfiles" \
            PATH="$HOME/.local/bin:${sops_dir}:/usr/local/bin:/usr/bin" \
            secrets get ANTHROPIC_API_KEY >/dev/null'
}
check_post_removal_agent "$(hostname -f)"
check_post_removal_agent devbox
```

Expected: the first line is exactly `resolved=/home/sami/.local/bin/secrets`; the PATH assertion passes; both SSH checks produce no output and exit `0`; and each EXIT trap restarts `secretsd.socket`. This is Gate E. It exercises the same failure surface that previously took down legion, envoy, the skill MCPs, and dojo: a non-interactive SSH command with no runtime directory and no broker dependency.

**Rollback:** If either post-removal check fails, restore only the two deleted paths before debugging any Rust or service behavior:

```bash
cd ~/.dotfiles
jj restore --from "$pre_shim_removal_revision" -- shims/secrets shims/tests/test_secrets_shim.py
source ~/.dotfiles/.bashrc
test "$(command -v secrets)" = "$HOME/.dotfiles/shims/secrets"
```

Expected: jj restores exactly the live bash client and its focused test, the shell resolves the shim again, and no other migrated secret, plugin, unit, or installer path changes. Do not use unscoped `jj restore`.

---

### Task 3: Prove the Rust client on both machines while the bash shim remains live

**Files:** none modified. This task is executable evidence only.

**Interfaces:**
- Consumes: Gate B, the still-present `~/.dotfiles/shims/secrets`, and the Rust client resolved by `mise which secrets`.
- Produces: evidence that the Rust agent tier works with neither `XDG_RUNTIME_DIR` nor a daemon, and that the human-tier client works on each target through the release-owned daemon.
- Gate C — **Rust agent client on every target:** an SSH command without `XDG_RUNTIME_DIR`, with the daemon socket stopped, obtains `ANTHROPIC_API_KEY` through the Rust client on the laptop and devbox. `shims/secrets` cannot be deleted until Gate C passes twice.
- Gate D — **Rust human client on every target:** a human observes the required physical touches and verifies cache, injection, list, grants, and lock on both machines. `shims/secrets` cannot be deleted until Gate D passes twice.

- [ ] **Step 1: Confirm the coexistence boundary before any Rust-client acceptance call**

Run on the laptop:

```bash
cd ~/.dotfiles
source .bashrc
rust_secrets="$(mise which secrets)"
printf 'shell=%s\nrust=%s\n' "$(command -v secrets)" "$rust_secrets"
test "$(command -v secrets)" = "$HOME/.dotfiles/shims/secrets"
test "$rust_secrets" != "$HOME/.dotfiles/shims/secrets"
test -x "$rust_secrets"
```

Expected: the first line is exactly `shell=/home/sami/.dotfiles/shims/secrets`; the second starts with `rust=/`, ends in `/secrets`, and does not contain `/.dotfiles/`; all three tests exit `0`. This establishes the only allowed coexistence mode: ordinary consumers still resolve the bash shim, while explicit Rust checks use `mise which secrets`.

- [ ] **Step 2: Run the mandatory minimal-environment SSH agent gate on both targets with the daemon unavailable**

Run from the laptop:

```bash
check_rust_agent_over_ssh() {
    local host="$1"
    ssh "$host" 'set -euo pipefail
        source "$HOME/.dotfiles/.bashrc"
        rust_secrets="$(mise which secrets)"
        sops_dir="$(dirname "$(mise which sops)")"
        systemctl --user stop secretsd.service secretsd.socket
        trap "systemctl --user start secretsd.socket" EXIT
        env -i \
            HOME="$HOME" \
            DOTFILES_DIR="$HOME/.dotfiles" \
            PATH="$(dirname "$rust_secrets"):${sops_dir}:/usr/local/bin:/usr/bin" \
            secrets get ANTHROPIC_API_KEY >/dev/null'
}
check_rust_agent_over_ssh "$(hostname -f)"
check_rust_agent_over_ssh devbox
```

Expected: no output and exit `0` for both invocations. Each remote command deliberately omits `XDG_RUNTIME_DIR`, uses `secrets get ANTHROPIC_API_KEY` rather than a direct test harness, and stops both the daemon and its socket before the call. The EXIT trap restores `secretsd.socket` even when the command fails. This is the mandatory regression check for the outage caused by a top-level `${XDG_RUNTIME_DIR:?}` under `set -u`; a missing runtime directory, a daemon connection attempt, a YubiKey touch, a failure to decrypt the agent tier, or a failure to restore the socket is a Gate C failure.

- [ ] **Step 3: Repeat Gate C from devbox to prove its own non-interactive surface**

Run:

```bash
ssh devbox 'set -euo pipefail
    source "$HOME/.dotfiles/.bashrc"
    rust_secrets="$(mise which secrets)"
    sops_dir="$(dirname "$(mise which sops)")"
    systemctl --user stop secretsd.service secretsd.socket
    trap "systemctl --user start secretsd.socket" EXIT
    env -i \
        HOME="$HOME" \
        DOTFILES_DIR="$HOME/.dotfiles" \
        PATH="$(dirname "$rust_secrets"):${sops_dir}:/usr/local/bin:/usr/bin" \
        secrets get ANTHROPIC_API_KEY >/dev/null'
```

Expected: no output and exit `0`; `secretsd.socket` is running again on return. This is intentionally redundant with the controller-side devbox command: the devbox itself must prove that its non-interactive shell, real mise executable paths, and agent-tier sops path cannot depend on the broker.

- [ ] **Step 4: Human-run Gate D on the laptop**

> **Human only.** This step causes physical YubiKey touches. Do not run it from an agent, do not automate it, and do not decrypt a `secrets.human.d/*.env` file. Restart OpenCode before this step so it loads the release-owned plugin path configured in Task 2.

In a human terminal on the laptop, run:

```bash
rust_secrets="$(mise which secrets)"
"$rust_secrets" get DEEL_API_KEY | wc -c
"$rust_secrets" get DEEL_API_KEY | wc -c
"$rust_secrets" DEEL_API_KEY -- sh -c 'test -n "$DEEL_API_KEY" && printf "injected\n"'
"$rust_secrets" list | grep -E '^(DEEL_API_KEY|PULUMI_CONFIG_PASSPHRASE)  \(human tier\)$'
"$rust_secrets" grants
"$rust_secrets" lock
"$rust_secrets" grants
```

Expected: the first `get` prompts for exactly one physical touch and prints a positive byte count; the second prints a positive byte count with no touch; injection prints `injected`; `list` prints the two displayed human-tier entries without a touch; `grants` reports the active grant before `lock`; `lock` exits `0`; and the final `grants` reports no active grant for `DEEL_API_KEY`. No secret value is displayed. A missing touch, a second touch for the cached read, any secret text in output, or an inability to use the release-owned plugin blocks Gate D.

- [ ] **Step 5: Human-run Gate D on devbox**

> **Human only.** First establish the normal `devbox` PC/SC tunnel so the persisted absolute `PCSCLITE_CSOCK_NAME` path is reachable. The required touch happens on the laptop's YubiKey; no desktop service or notifier is involved.

Run from the laptop and perform the prompted physical touches:

```bash
ssh devbox 'set -euo pipefail
    rust_secrets="$(mise which secrets)"
    "$rust_secrets" get PULUMI_CONFIG_PASSPHRASE | wc -c
    "$rust_secrets" get PULUMI_CONFIG_PASSPHRASE | wc -c
    "$rust_secrets" PULUMI_CONFIG_PASSPHRASE -- sh -c '\''test -n "$PULUMI_CONFIG_PASSPHRASE" && printf "injected\n"'\''
    "$rust_secrets" list | grep -E '\''^(DEEL_API_KEY|PULUMI_CONFIG_PASSPHRASE)  \(human tier\)$'\''
    "$rust_secrets" grants
    "$rust_secrets" lock
    "$rust_secrets" grants'
```

Expected: the first `get` emits a positive byte count after one laptop YubiKey touch; the second is a no-touch cached read; injection prints `injected`; list prints both human-tier names with no touch; grants reports the active request before lock and no active grant for `PULUMI_CONFIG_PASSPHRASE` afterwards. A `YUBIKEY_UNREACHABLE`, `INTERNAL`, timeout, unexpected touch, or plaintext disclosure fails Gate D; return to Task 2 and inspect the absolute `PCSCLITE_CSOCK_NAME`, real `SECRETSD_SOPS_BIN`, and PATH entries rather than changing the shim.

**Rollback:** Tasks 3–5 modify no tracked file. If either gate fails, ordinary consumers remain on `~/.dotfiles/shims/secrets`; do not remove it. Restore the named Task 2 backups with its rollback block when the release wiring is at fault, then rerun Gate B followed by Gate C or Gate D. No `sops -d` command against `secrets.human.d` is part of rollback.

---

### Task 2: Reduce the installer to deployment-only systemd and plugin wiring

**Files:**
- Modify: `installers/secretsd.sh`
- Modify at install time: `opencode/opencode.json`
- Create or replace at install time: `~/.config/systemd/user/secretsd.service`, `~/.config/systemd/user/secretsd.socket`, and `~/.config/systemd/user/secretsd.service.d/deployment.conf`
- Preserve before replacement when present: `~/.config/systemd/user/secretsd.service.before-rust-cutover`, `~/.config/systemd/user/secretsd.socket.before-rust-cutover`, and `~/.config/systemd/user/secretsd.service.d/deployment.conf.before-rust-cutover`

**Interfaces:**
- Consumes: Gate A; `mise which secretsd`; `mise which secrets`; `mise which sops`; `mise which age-plugin-yubikey`; generic release units adjacent to the real `secretsd` executable; and the release-owned plugin path `~/.local/share/secretsd/opencode/plugins/secretsd.ts`.
- Produces: generic packaged unit links, a per-machine deployment drop-in, an enabled `secretsd.socket`, and precisely one OpenCode plugin reference to the release-owned path.
- Gate B — **Deployment wiring is complete:** both units link to the release, the socket is enabled without enabling the service, the drop-in holds resolved absolute paths, and OpenCode references only the release-owned plugin. Task 3 is blocked until this gate passes on each target.

- [ ] **Step 1: Preserve the devbox's hand-written deployment before the installer replaces any user-unit path**

The devbox currently has a debug hand-written unit with four absolute paths. It is evidence and the immediate rollback source, not disposable setup. The replacement installer below creates the three `*.before-rust-cutover` backups only when their corresponding destination exists and no backup exists. Do not pre-delete or overwrite these backups on either machine.

- [ ] **Step 2: Replace `installers/secretsd.sh` with deployment-only wiring**

Replace the entire file with:

```bash
#!/bin/bash
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

readonly OPENCODE_JSON="${DOTFILES_DIR}/opencode/opencode.json"
readonly USER_UNIT_DIR="${HOME}/.config/systemd/user"
readonly DROPIN_DIR="${USER_UNIT_DIR}/secretsd.service.d"
readonly DROPIN="${DROPIN_DIR}/deployment.conf"

real_mise_binary() {
    local tool="$1" path mise_data_dir
    path="$(mise which "$tool" 2>/dev/null)" || return 1
    path="$(readlink -f "$path")"
    mise_data_dir="${MISE_DATA_DIR:-${HOME}/.mise}"
    [[ "$path" == /* && -x "$path" && "$path" != "${mise_data_dir}/shims/"* ]] || return 1
    printf '%s\n' "$path"
}

backup_once() {
    local path="$1" backup="${1}.before-rust-cutover"
    if { [[ -e "$path" ]] || [[ -L "$path" ]]; } && [[ ! -e "$backup" && ! -L "$backup" ]]; then
        cp -a --no-dereference "$path" "$backup"
        printf 'secretsd: preserved %s\n' "$backup"
    fi
}

configuration_changed=0
link_release_unit() {
    local unit="$1" source="$2" destination="${USER_UNIT_DIR}/${unit}"
    if [[ ! -L "$destination" || "$(readlink "$destination")" != "$source" ]]; then
        backup_once "$destination"
        ensure_link "$source" "$destination"
        configuration_changed=1
    fi
}

if ! secretsd_bin="$(real_mise_binary secretsd)" || ! secrets_bin="$(real_mise_binary secrets)"; then
    printf '%s\n' 'secretsd: v0.1.0 is not installed by mise; skipping systemd, drop-in, and plugin wiring.'
    return 0 2>/dev/null || exit 0
fi

release_dir="$(dirname "$secretsd_bin")"
service_source="${release_dir}/systemd/secretsd.service"
socket_source="${release_dir}/systemd/secretsd.socket"
if [[ ! -f "$service_source" || ! -f "$socket_source" || ! -f "${HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts" ]]; then
    printf '%s\n' 'secretsd: installed release is incomplete; expected units and release-owned plugin are missing.' >&2
    return 1 2>/dev/null || exit 1
fi

sops_bin="$(real_mise_binary sops)" || {
    printf '%s\n' 'secretsd: mise did not resolve a real sops executable.' >&2
    return 1 2>/dev/null || exit 1
}
age_plugin_bin="$(real_mise_binary age-plugin-yubikey)" || {
    printf '%s\n' 'secretsd: mise did not resolve a real age-plugin-yubikey executable.' >&2
    return 1 2>/dev/null || exit 1
}
dotfiles_path="$(cd "$DOTFILES_DIR" && pwd -P)"
human_dir="${dotfiles_path}/secrets.human.d"
age_plugin_dir="$(dirname "$age_plugin_bin")"
secretsd_dir="$(dirname "$secretsd_bin")"
service_path="${secretsd_dir}:${age_plugin_dir}:${HOME}/.local/bin:/usr/local/bin:/usr/bin"

if [[ -n "${PCSCLITE_CSOCK_NAME:-}" && "${PCSCLITE_CSOCK_NAME}" != /* ]]; then
    printf '%s\n' 'secretsd: PCSCLITE_CSOCK_NAME must be an absolute path.' >&2
    return 1 2>/dev/null || exit 1
fi

mkdir -p "$USER_UNIT_DIR" "$DROPIN_DIR"
link_release_unit secretsd.service "$service_source"
link_release_unit secretsd.socket "$socket_source"
backup_once "$DROPIN"
dropin_tmp="$(mktemp "${DROPIN_DIR}/.deployment.conf.XXXXXX")"
{
    printf '[Service]\n'
    printf 'Environment=SECRETSD_HUMAN_DIR=%s\n' "$human_dir"
    printf 'Environment=SECRETSD_SOPS_BIN=%s\n' "$sops_bin"
    printf 'Environment=PATH=%s\n' "$service_path"
    if [[ -n "${PCSCLITE_CSOCK_NAME:-}" ]]; then
        printf 'Environment=PCSCLITE_CSOCK_NAME=%s\n' "$PCSCLITE_CSOCK_NAME"
    fi
} >"$dropin_tmp"
if ! cmp -s "$dropin_tmp" "$DROPIN"; then
    install -m 0644 "$dropin_tmp" "$DROPIN"
    configuration_changed=1
fi
rm -f "$dropin_tmp"

ensure_json "$OPENCODE_JSON" \
    '.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)' \
    '.plugin = ([.plugin[] | select(. != "file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts")] | if index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") then . else . + ["file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts"] end)' \
    'Registering the release-owned secretsd OpenCode plugin...'

systemctl --user daemon-reload
systemctl --user enable --now secretsd.socket
if (( configuration_changed )); then
    systemctl --user try-restart secretsd.service
    printf '%s\n' 'secretsd: configuration changed; any existing human-tier grants were cleared.'
fi
printf 'secretsd: Rust client available at %s\n' "$secrets_bin"
```

This removes every old responsibility: no GitHub query, no `curl`, no `tar`, no local Cargo build preference, no `bin/secretsd` copy, no tag marker, no vendored units, no manager-environment import, and no dotfiles-owned plugin source. mise owns release retrieval; this installer performs only the local wiring mise cannot perform.

**Why every value in the drop-in is absolute:** systemd user services start with only `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, not the interactive shell's PATH. On the devbox, `sops` was a mise shim outside that PATH; the daemon could not spawn it and returned a silent `INTERNAL` because a spawn failure has no child stderr to log. Adding a shim directory would still be wrong: the shim re-execs `mise`, which is itself unavailable in the minimal service environment. The real executable from `mise which sops` is mandatory. The same failure then occurred for `age-plugin-yubikey`, so the drop-in `PATH` must prepend the real `secretsd` directory and the real plugin directory from `dirname "$(mise which age-plugin-yubikey)"`. The devbox additionally persists the absolute `PCSCLITE_CSOCK_NAME`; a directly attached laptop YubiKey has no such line. Do not replace any of these with `~`, `$HOME`, `$(...)`, a mise shim, `systemctl --user import-environment`, or a relative path.

- [ ] **Step 3: Validate the reduced installer and establish Gate B on each target**

Run on the laptop, then run through the existing `devbox` SSH host:

```bash
cd ~/.dotfiles
bash -n installers/secretsd.sh
mise exec shellcheck -- shellcheck -x --source-path=SCRIPTDIR installers/secretsd.sh
bash installers/secretsd.sh
systemctl --user is-enabled secretsd.socket
if systemctl --user is-enabled --quiet secretsd.service; then
    printf '%s\n' 'FAIL: secretsd.service must not be enabled' >&2
    exit 1
fi
jq -e '.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)' opencode/opencode.json
systemctl --user cat secretsd.service
```

Expected: the syntax and ShellCheck commands are silent and exit `0`; the installer reports a Rust client path; `is-enabled secretsd.socket` prints `enabled`; the service-enabled guard prints nothing and exits `0`; the jq check prints `true`; and `systemctl cat` includes the generic `ExecStart=secretsd` plus a `deployment.conf` with absolute `SECRETSD_HUMAN_DIR`, `SECRETSD_SOPS_BIN`, and `PATH` lines. On the devbox it also includes an absolute `PCSCLITE_CSOCK_NAME` line. It must not show `systemctl --user enable secretsd.service` having been run.

Run the identical block remotely for the devbox:

```bash
ssh devbox 'cd ~/.dotfiles && bash -n installers/secretsd.sh && mise exec shellcheck -- shellcheck -x --source-path=SCRIPTDIR installers/secretsd.sh && bash installers/secretsd.sh && systemctl --user is-enabled secretsd.socket && ! systemctl --user is-enabled --quiet secretsd.service && jq -e '\''.plugin | (index("file://{env:HOME}/.local/share/secretsd/opencode/plugins/secretsd.ts") != null and index("file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts") == null)'\'' opencode/opencode.json'
```

Expected: `enabled` and `true` are printed; all preceding checks are silent and every command exits `0`. Any other result fails Gate B. Keep the bash shim first on PATH and restore the preserved units/drop-in before retrying.

**Rollback:** Restore only the backed-up external paths, reload the manager, and keep the bash shim untouched:

```bash
for path in secretsd.service secretsd.socket; do
    destination="$HOME/.config/systemd/user/$path"
    backup="${destination}.before-rust-cutover"
    if [[ -e "$backup" || -L "$backup" ]]; then
        rm -f "$destination"
        mv "$backup" "$destination"
    fi
done
dropin="$HOME/.config/systemd/user/secretsd.service.d/deployment.conf"
backup="${dropin}.before-rust-cutover"
if [[ -f "$backup" ]]; then
    install -m 0644 "$backup" "$dropin"
else
    rm -f "$dropin"
fi
systemctl --user daemon-reload
systemctl --user try-restart secretsd.service
jj restore --from "$pre_cutover_revision" -- installers/secretsd.sh opencode/opencode.json
```

Expected: every pre-existing unit and drop-in is restored from its named backup, no unrelated user-unit is changed, the current service restarts with the restored configuration, and jj restores only the installer and OpenCode configuration paths. The active bash shim remains available throughout.

---

### Task 1: Establish the release artifact and pin it through mise

**Files:**
- Modify: `mise.toml`
- Read: the `v0.1.0` GitHub release asset for `sjawhar/secretsd`

**Interfaces:**
- Consumes: a published `secretsd-v0.1.0-linux-x86_64.tar.gz` release asset.
- Produces: real executables returned by `mise which secretsd` and `mise which secrets`, and the release-owned plugin at `~/.local/share/secretsd/opencode/plugins/secretsd.ts`.
- Gate A — **Release artifact is installable:** the archive contains both binaries, the two generic systemd units, and the relocated plugin. Tasks 2–5 are blocked until this gate passes.

- [ ] **Step 1: Prove the `v0.1.0` release contains the complete consumer payload before editing dotfiles**

Run:

```bash
release_dir="$(mktemp -d)"
trap 'rm -rf "$release_dir"' EXIT
gh release download v0.1.0 \
  --repo sjawhar/secretsd \
  --pattern secretsd-v0.1.0-linux-x86_64.tar.gz \
  --dir "$release_dir"
tar tzf "$release_dir/secretsd-v0.1.0-linux-x86_64.tar.gz" | sort
```

Expected:

```text
secretsd-v0.1.0-linux-x86_64/opencode/plugins/secretsd.ts
secretsd-v0.1.0-linux-x86_64/secrets
secretsd-v0.1.0-linux-x86_64/secretsd
secretsd-v0.1.0-linux-x86_64/systemd/secretsd.service
secretsd-v0.1.0-linux-x86_64/systemd/secretsd.socket
```

The tag must exist **before** adding the mise pin: mise's `github:` backend installs a GitHub **release asset**, not a source checkout or a local Cargo build. A missing item or a failed download fails Gate A; do not replace it with a dotfiles-side build, copy, or source-checkout link. Publish the complete `v0.1.0` release asset in `secretsd`, rerun this command, and proceed only when the five required payload paths appear.

- [ ] **Step 2: Pin the release in the existing direct-GitHub style**

In the `[tools]` table of `mise.toml`, directly after the other `github:sjawhar/*` entries, add:

```toml
"github:sjawhar/secretsd" = "v0.1.0"  # Rust secrets CLI, daemon, generic units, and OpenCode plugin
```

Run:

```bash
pre_cutover_revision="$(jj log -r @ -T 'change_id ++ "\n"')"
printf 'pre-cutover revision: %s\n' "$pre_cutover_revision"
grep -Fx '"github:sjawhar/secretsd" = "v0.1.0"  # Rust secrets CLI, daemon, generic units, and OpenCode plugin' mise.toml
mise install github:sjawhar/secretsd@v0.1.0
rust_secretsd="$(mise which secretsd)"
rust_secrets="$(mise which secrets)"
printf 'secretsd=%s\nsecrets=%s\n' "$rust_secretsd" "$rust_secrets"
test -x "$rust_secretsd"
test -x "$rust_secrets"
test -f "$HOME/.local/share/secretsd/opencode/plugins/secretsd.ts"
```

Expected: the first line reproduces the pin exactly; `mise install` exits `0`; both printed paths are absolute executable paths in mise's install directory, not paths in `~/.dotfiles/shims`; and the final three tests exit `0` without a YubiKey touch.

- [ ] **Step 3: Record the no-release behavior that keeps installs non-fatal**

Keep the reduced `installers/secretsd.sh` from Task 2 gated on both `mise which secretsd` and `mise which secrets` succeeding. If either command fails because no release asset exists yet, it must print exactly:

```text
secretsd: v0.1.0 is not installed by mise; skipping systemd, drop-in, and plugin wiring.
```

and return `0` without changing unit links, a drop-in, or `opencode/opencode.json`. This follows the non-fatal release-absence behavior of `installers/voxtype.sh`; `installers/mise.sh` already continues after a failed `mise install` attempt. Once the release is published, rerunning `mise install github:sjawhar/secretsd@v0.1.0` followed by `bash installers/secretsd.sh` must perform the wiring normally.

**Rollback:** The release check and `mise install` do not alter tracked secret material. If the pin must be removed before the final cutover commit, run `jj restore --from "$pre_cutover_revision" -- mise.toml`. Expected: `mise.toml` returns to its recorded version and no other path is restored.

---
