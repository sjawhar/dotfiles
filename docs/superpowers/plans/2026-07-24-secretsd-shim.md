# secretsd Shim Implementation Plan

> **For agentic workers:** Execute the checkbox steps in order. This plan changes only `shims/secrets` and its focused test; it never decrypts the human tier outside the broker.

**Goal:** Route human-tier secret access through `secretsd` while preserving zero-interaction, broker-free agent-tier decryption.

**Scope:** Replace the legacy `/dev/shm` human cache in `shims/secrets` with socket routing and add `scripts/test-secrets-shim`. Do not change the daemon, the plugin, human ciphertext, or sibling plans.

## Non-negotiable contracts

- **Agent tier stays direct:** `secrets.env` and optional `secrets.local.env` keep using `sops -d` and never contact the broker or require a YubiKey touch.
- **Human tier is filename-routed:** the keys are the basenames of `secrets.human.d/<KEY>.env`; the shim never decrypts that directory itself.
- **Token-file contract:** the plugin writes `${XDG_RUNTIME_DIR}/secretsd/<sessionID>.token` and injects only `SECRETSD_SESSION_TOKEN_FILE`. The shim treats that variable as a **path** and reads the token from that file; it must not introduce a token-value environment variable.
- **No HELLO per request:** each socket connection carries one request, so the shim sends only the requested operation frame.
- **Control frames carry no scope data:** `GRANTS`, `DENY`, and `LOCK` send neither token nor tty.
- **Hard rollout gate:** deploy this rewritten shim only after the human migration ceremony has created all two per-key files and `scripts/verify-sops-recipients` exits `0`. Until then `secrets.human.env` is the only source of human keys, so deploying filename routing would make them unavailable.

---

## Task 1: Replace `shims/secrets`

**Files:** Modify `shims/secrets`.

- [ ] **Step 1: Install the complete shim implementation**

Replace `shims/secrets` with the complete implementation in the next section.

```bash
#!/usr/bin/env bash
# Route human-tier secrets through secretsd; keep the agent tier direct.
set -euo pipefail

DOTFILES_DIR="${DOTFILES_DIR:-${HOME}/.dotfiles}"
SECRETS_FILE="${DOTFILES_DIR}/secrets.env"
SECRETS_LOCAL_FILE="${DOTFILES_DIR}/secrets.local.env"
HUMAN_DIR="${DOTFILES_DIR}/secrets.human.d"
SECRETSD_SOCKET="${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is required}/secretsd.sock"
SECRETSD_TIMEOUT_SECONDS=90

fail() {
    printf 'secrets: %s\n' "$*" >&2
    return 1
}

agent_dump() {
    if [[ -f "$SECRETS_LOCAL_FILE" ]]; then
        sops -d --output-type dotenv "$SECRETS_LOCAL_FILE"
    fi
    sops -d --output-type dotenv "$SECRETS_FILE"
}

# Emit the first matching assignment. The local overlay is emitted first and wins.
agent_assignment() {
    local key="$1"
    agent_dump | awk -F= -v key="$key" '$1 == key { print; exit }'
}

human_key_exists() {
    local wanted="$1" file name
    [[ -d "$HUMAN_DIR" ]] || return 1
    shopt -s nullglob
    for file in "$HUMAN_DIR"/*.env; do
        name="${file##*/}"
        name="${name%.env}"
        [[ "$name" == "$wanted" ]] && return 0
    done
    return 1
}

session_token() {
    local token_file="${SECRETSD_SESSION_TOKEN_FILE:-}" token
    [[ -n "$token_file" ]] || return 1
    [[ -r "$token_file" ]] || {
        fail "session token file is unreadable: ${token_file}"
        return 1
    }
    token="$(<"$token_file")"
    [[ -n "$token" ]] || {
        fail "session token file is empty: ${token_file}"
        return 1
    }
    printf '%s\n' "$token"
}

request_scope_suffix() {
    local token tty_path
    if [[ -n "${SECRETSD_SESSION_TOKEN_FILE:-}" ]]; then
        token="$(session_token)" || return 1
        printf '\ttoken=%s' "$token"
        return 0
    fi
    if tty_path="$(tty 2>/dev/null)"; then
        printf '\ttty=%s' "$tty_path"
    fi
}

broker_exchange() {
    local request="$1" response_file="$2"
    if ! printf '%s\n' "$request" \
        | timeout "$SECRETSD_TIMEOUT_SECONDS" socat - "UNIX-CONNECT:${SECRETSD_SOCKET}" >"$response_file" 2>/dev/null; then
        fail "broker unavailable or request timed out; re-approval may be required"
        return 1
    fi
}

# Writes the declared raw payload to $2. It accepts only an exact-byte response.
BROKER_PAYLOAD_LENGTH=0
decode_framed_payload() {
    local response_file="$1" payload_file="$2" header declared header_bytes actual tab error_fields code message
    tab=$'\t'
    if ! IFS= read -r header <"$response_file"; then
        fail "broker returned an empty response"
        return 1
    fi
    if [[ "$header" == "ERR${tab}"* ]]; then
        error_fields="${header#"ERR${tab}"}"
        if [[ "$error_fields" != *"$tab"* ]]; then
            fail "broker returned an invalid error response"
            return 1
        fi
        code="${error_fields%%"${tab}"*}"
        message="${error_fields#*"${tab}"}"
        fail "broker rejected request: ${code} (${message})"
        return 1
    fi
    if [[ "$header" != "OK${tab}"len=* ]]; then
        fail "broker returned an invalid response header"
        return 1
    fi
    declared="${header#"OK${tab}len="}"
    if [[ ! "$declared" =~ ^[0-9]+$ ]]; then
        fail "broker returned an invalid payload length"
        return 1
    fi
    header_bytes="$(LC_ALL=C printf '%s\n' "$header" | wc -c)"
    tail -c "+$((header_bytes + 1))" "$response_file" >"$payload_file"
    actual="$(LC_ALL=C wc -c <"$payload_file")"
    actual="${actual//[[:space:]]/}"
    if (( 10#$actual != 10#$declared )); then
        fail "broker payload length mismatch (declared ${declared}, recovered ${actual})"
        return 1
    fi
    BROKER_PAYLOAD_LENGTH="$declared"
}

# Print a GET value only after its raw byte count AND the count that Bash
# recovered agree with the daemon's declared length. Command substitution cannot
# retain NUL, so that condition rejects it instead of corrupting it.
human_value() {
    local key="$1" request response_file payload_file value recovered declared
    response_file="$(mktemp)"
    payload_file="$(mktemp)"
    request=$'GET\tkey='
    request+="$key"
    request+="$(request_scope_suffix)" || {
        rm -f "$response_file" "$payload_file"
        return 1
    }
    if ! broker_exchange "$request" "$response_file" \
        || ! decode_framed_payload "$response_file" "$payload_file"; then
        rm -f "$response_file" "$payload_file"
        return 1
    fi
    declared="$BROKER_PAYLOAD_LENGTH"
    value="$(<"$payload_file")"
    recovered="$(LC_ALL=C printf '%s' "$value" | wc -c)"
    recovered="${recovered//[[:space:]]/}"
    rm -f "$response_file" "$payload_file"
    if (( 10#$recovered != 10#$declared )); then
        fail "broker payload length mismatch after Bash recovery (declared ${declared}, recovered ${recovered})"
        return 1
    fi
    printf '%s' "$value"
}

# Metadata responses (GRANTS) can safely stream verbatim after framing validation.
broker_print_payload() {
    local request="$1" response_file payload_file
    response_file="$(mktemp)"
    payload_file="$(mktemp)"
    if ! broker_exchange "$request" "$response_file" \
        || ! decode_framed_payload "$response_file" "$payload_file"; then
        rm -f "$response_file" "$payload_file"
        return 1
    fi
    cat "$payload_file"
    rm -f "$response_file" "$payload_file"
}

broker_expect_ok() {
    local request="$1" response_file header response_bytes expected_bytes
    response_file="$(mktemp)"
    if ! broker_exchange "$request" "$response_file"; then
        rm -f "$response_file"
        return 1
    fi
    if ! IFS= read -r header <"$response_file" || [[ "$header" != "OK" ]]; then
        rm -f "$response_file"
        fail "broker returned an invalid control response"
        return 1
    fi
    response_bytes="$(LC_ALL=C wc -c <"$response_file")"
    expected_bytes="$(LC_ALL=C printf 'OK\n' | wc -c)"
    response_bytes="${response_bytes//[[:space:]]/}"
    expected_bytes="${expected_bytes//[[:space:]]/}"
    rm -f "$response_file"
    if (( 10#$response_bytes != 10#$expected_bytes )); then
        fail "broker returned extra bytes after control response"
        return 1
    fi
}

secret_value() {
    local key="$1" agent_line
    agent_line="$(agent_assignment "$key")"
    if human_key_exists "$key"; then
        if [[ -n "$agent_line" ]]; then
            fail "key '${key}' exists in both agent and human tiers; refusing ambiguous access"
            return 1
        fi
        human_value "$key"
        return 0
    fi
    if [[ -n "$agent_line" ]]; then
        printf '%s' "${agent_line#*=}"
        return 0
    fi
    fail "secret '${key}' not found"
}

list_keys() {
    local agent_names key file name
    agent_names="$(agent_dump | awk -F= 'NF && $1 !~ /^#/ { print $1 }' | sort -u)"
    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        if human_key_exists "$key"; then
            fail "key '${key}' exists in both agent and human tiers; refusing ambiguous metadata"
            return 1
        fi
    done <<<"$agent_names"
    [[ -z "$agent_names" ]] || printf '%s\n' "$agent_names"
    if [[ -d "$HUMAN_DIR" ]]; then
        shopt -s nullglob
        for file in "$HUMAN_DIR"/*.env; do
            name="${file##*/}"
            name="${name%.env}"
            printf '%s  (human tier)\n' "$name"
        done
    fi
}

case "${1:-}" in
    edit)       exec sops "$SECRETS_FILE" ;;
    edit-local) exec sops "$SECRETS_LOCAL_FILE" ;;
    edit-human)
        key="${2:?usage: secrets edit-human KEY}"
        exec sops "${HUMAN_DIR}/${key}.env"
        ;;
    list)
        list_keys
        exit
        ;;
    grants)
        broker_print_payload "GRANTS"
        exit
        ;;
    deny)
        id="${2:?usage: secrets deny ID}"
        broker_expect_ok $'DENY\tid='"$id"
        exit
        ;;
    lock)
        broker_expect_ok "LOCK"
        exit
        ;;
    get)
        key="${2:?usage: secrets get KEY}"
        secret_value "$key"
        printf '\n'
        exit
        ;;
esac

keys=()
while [[ $# -gt 0 && "$1" != "--" ]]; do
    keys+=("$1")
    shift
done
[[ "${1:-}" == "--" ]] && shift
if (( ${#keys[@]} == 0 || $# == 0 )); then
    fail "usage: secrets KEY1 [KEY2 ...] -- command [args...]"
    exit 1
fi

env_args=()
for key in "${keys[@]}"; do
    value="$(secret_value "$key")" || exit 1
    env_args+=("${key}=${value}")
done
exec env "${env_args[@]}" "$@"
```

The framed read intentionally is **not binary-safe**. Human-tier values are dotenv/environment values and must not contain NUL: `execve` cannot represent a NUL in an environment string. The daemon currently preserves bytes after `=` through the line terminator, so the companion daemon-side change (owned separately) rejects NUL at `parse_single_assignment()`; this shim independently detects Bash's lossy recovery by comparing its recovered byte count with `len` and errors rather than returning a corrupted secret. It also rejects a short payload and any bytes beyond `len`.

- [ ] **Step 2: Verify shell syntax and ShellCheck**

Run:

```bash
bash -n shims/secrets
mise exec shellcheck -- shellcheck shims/secrets
```

Expected: both commands exit `0` with no output.

---

## Task 2: Add a protocol-focused shim test

**Files:** Create `scripts/test-secrets-shim`.

The test uses a one-request Python Unix-socket fixture so every assertion sees the exact bytes the shim sent and can control the raw daemon response. It never decrypts a human-tier file or contacts a real broker.

- [ ] **Step 1: Create the complete test script**

```bash
#!/usr/bin/env bash
# Protocol tests for shims/secrets. No real sops decrypt or secretsd instance.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
fixture="${workdir}/fixture"
runtime="${workdir}/runtime"
fake_bin="${workdir}/bin"
socket="${runtime}/secretsd.sock"
response="${workdir}/response"
request="${workdir}/request"
token_file="${runtime}/session.token"
broker_pid=""

cleanup() {
    [[ -z "$broker_pid" ]] || kill "$broker_pid" 2>/dev/null || true
    rm -rf "$workdir"
}
trap cleanup EXIT

die() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_equal() {
    local expected="$1" actual="$2" label="$3"
    [[ "$actual" == "$expected" ]] || die "${label}: expected ${expected@Q}, got ${actual@Q}"
}

assert_contains() {
    local needle="$1" haystack="$2" label="$3"
    [[ "$haystack" == *"$needle"* ]] || die "${label}: missing ${needle@Q} in ${haystack@Q}"
}

assert_request() {
    local expected="$1" label="$2"
    cmp -s <(printf '%s' "$expected") "$request" || die "${label}: wrong request frame"
}

start_broker() {
    rm -f "$socket" "$request"
    python3 - "$socket" "$response" "$request" <<'PY' &
import socket
import sys
from pathlib import Path

socket_path, response_path, request_path = sys.argv[1:]
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(socket_path)
listener.listen(1)
connection, _ = listener.accept()
request = b""
while not request.endswith(b"\n"):
    chunk = connection.recv(4096)
    if not chunk:
        break
    request += chunk
Path(request_path).write_bytes(request)
connection.sendall(Path(response_path).read_bytes())
connection.close()
listener.close()
PY
    broker_pid="$!"
    for _ in {1..100}; do
        [[ -S "$socket" ]] && return 0
        sleep 0.01
    done
    die "fake broker did not create its socket"
}

wait_for_broker() {
    wait "$broker_pid"
    broker_pid=""
}

run_with_token() {
    env PATH="${fake_bin}:${PATH}" \
        DOTFILES_DIR="$fixture" \
        XDG_RUNTIME_DIR="$runtime" \
        SECRETSD_SESSION_TOKEN_FILE="$token_file" \
        "${ROOT}/shims/secrets" "$@"
}

run_without_token() {
    env -u SECRETSD_SESSION_TOKEN_FILE \
        PATH="${fake_bin}:${PATH}" \
        DOTFILES_DIR="$fixture" \
        XDG_RUNTIME_DIR="$runtime" \
        "${ROOT}/shims/secrets" "$@"
}

mkdir -p "$fixture/secrets.human.d" "$runtime" "$fake_bin"
touch "$fixture/secrets.env" "$fixture/secrets.human.d/HUMAN.env" "$fixture/secrets.human.d/DUP.env"
printf 'test-token\n' >"$token_file"
cat >"${fake_bin}/sops" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'AGENT_ONLY=agent-value' 'DUP=agent-copy'
EOF
chmod +x "${fake_bin}/sops"

# GRANTS is payload-framed by the daemon, not an OK fields response.
printf 'OK\tlen=20\nid=17 state=pending\n' >"$response"
start_broker
output="$(run_without_token grants)"
wait_for_broker
assert_equal 'id=17 state=pending' "$output" 'GRANTS payload'
assert_request $'GRANTS\n' 'GRANTS'

# Control operations are deliberately scope-free and reply with exactly OK\n.
printf 'OK\n' >"$response"
start_broker
run_without_token deny 17
wait_for_broker
assert_request $'DENY\tid=17\n' 'DENY'

printf 'OK\n' >"$response"
start_broker
run_without_token lock
wait_for_broker
assert_request $'LOCK\n' 'LOCK'

printf 'OK\tlen=5\nhuman' >"$response"
start_broker
output="$(run_with_token get HUMAN)"
wait_for_broker
assert_equal 'human' "$output" 'human GET value'
assert_request $'GET\tkey=HUMAN\ttoken=test-token\n' 'GET'

# A duplicate must be rejected before the shim can contact a broker.
if output="$(run_with_token get DUP 2>&1)"; then
    die 'duplicate key unexpectedly succeeded'
fi
assert_contains 'exists in both agent and human tiers' "$output" 'duplicate key'

# A short payload, extra payload bytes, and a NUL all fail closed. In the NUL
# case Bash drops the NUL during command substitution; the recovery count check
# is what turns that lossy conversion into an error.
printf 'OK\tlen=4\nabc' >"$response"
start_broker
if output="$(run_with_token get HUMAN 2>&1)"; then
    die 'short payload unexpectedly succeeded'
fi
wait_for_broker
assert_contains 'payload length mismatch' "$output" 'short payload'

printf 'OK\tlen=3\nabcEXTRA' >"$response"
start_broker
if output="$(run_with_token get HUMAN 2>&1)"; then
    die 'extra payload unexpectedly succeeded'
fi
wait_for_broker
assert_contains 'payload length mismatch' "$output" 'extra payload'

printf 'OK\tlen=3\nA\0B' >"$response"
start_broker
if output="$(run_with_token get HUMAN 2>&1)"; then
    die 'NUL payload unexpectedly succeeded'
fi
wait_for_broker
assert_contains 'payload length mismatch after Bash recovery' "$output" 'NUL payload'

printf 'PASS: secrets shim protocol tests\n'
```

Then run:

```bash
chmod +x scripts/test-secrets-shim
bash -n scripts/test-secrets-shim
mise exec shellcheck -- shellcheck scripts/test-secrets-shim
scripts/test-secrets-shim
```

Expected: the syntax and ShellCheck commands are silent and the test ends with `PASS: secrets shim protocol tests`.

---

## Task 3: Deploy only after the migration gate

**Files:** None. This is an ordering gate, not a compatibility mode.

- [ ] **Step 1: Confirm the human migration is complete before deploying the shim**

Run without decrypting any human-tier file:

```bash
cd ~/.dotfiles
for key in DEEL_API_KEY PULUMI_CONFIG_PASSPHRASE; do
    test -f "secrets.human.d/${key}.env" || {
        printf 'missing migrated human key: %s\n' "$key" >&2
        exit 1
    }
done
DOTFILES_DIR="$PWD" scripts/verify-sops-recipients
```

Expected: all two files exist and the verifier exits `0`. **Only after both conditions hold may this shim be deployed.** There is intentionally no fallback to `secrets.human.env`: retaining two human-tier sources would conceal an incomplete migration and make routing ambiguous. The migration plan deletes `secrets.human.env` only after its end-to-end checks pass.

---

## Behavior decisions

1. **`list` fails on cross-tier duplicates.** It is not metadata-only: a duplicate name can otherwise make `list` claim a key is human while `get`/`KEY -- command` reject it. The shim derives human names from filenames without a decrypt, decrypts only the agent tier, and exits non-zero if their intersection is non-empty. This gives the same fail-closed signal on every access surface while keeping `list` touchless.
2. **Framed payloads must have exactly the declared length.** `decode_framed_payload` rejects both a short response and trailing bytes. It never slices a response to `len` and proceeds.
3. **NUL is an invalid human-tier value.** The shim detects it even if a malformed/old daemon emits it; the companion daemon change rejects it at source. This is a compatibility boundary, not a binary transport promise.

---

## Final verification and single commit

- [ ] **Step 1: Run the complete focused verification**

```bash
bash -n shims/secrets
bash -n scripts/test-secrets-shim
mise exec shellcheck -- shellcheck shims/secrets scripts/test-secrets-shim
scripts/test-secrets-shim
jj diff --git -- shims/secrets scripts/test-secrets-shim
```

Expected: syntax and ShellCheck are clean; the protocol test passes; the diff contains only the shim and its focused test.

- [ ] **Step 2: Commit and push the one shim change**

```bash
jj describe -m "feat(secrets): route human-tier access through secretsd"
jj bookmark list
jj tug
jj git push
```

Expected: one commit containing only `shims/secrets` and `scripts/test-secrets-shim` is pushed to `main`.

## Self-review

- Agent-tier reads remain direct `sops` calls and have no socket dependency.
- Human-key discovery reads only `secrets.human.d/*.env` names; human ciphertext is never decrypted by the shim.
- GET uses an exact `OK\tlen=<n>\n` frame; GRANTS uses the same payload framing; DENY and LOCK require `OK\n`.
- GRANTS, DENY, and LOCK test their exact tokenless/tty-less request bytes.
- A pre-migration deployment is blocked explicitly, rather than relying on a hidden compatibility fallback.
- Every payload length mismatch, including a NUL lost by Bash command substitution, exits non-zero without returning a value.
