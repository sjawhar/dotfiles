# GH Credential Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-repo GitHub credential routing for agent shells, driven by `~/.gitconfig`. Same routing data feeds both `git push` (HTTPS) and `gh` CLI. Interactive shells unchanged.

**Architecture:** Profiles in `.gitconfig` (always present, inert), routes in a separate `gh-app-routes.gitconfig` (only loaded when `cc`/`oc` injects an `include.path` via `GIT_CONFIG_*` env vars). Native git matching dispatches the right helper. The `gh` shim consults `git config --get-urlmatch` to pick the same identity for `gh` API calls.

**Tech Stack:** Bash (cc/oc launchers, gh shim, smoke tests), Python 3 with PyJWT (gh-app-token credential helper, ported from existing `shims/git-credential-gh-app`), `jj` (Jujutsu) for version control, `secrets` (sops-backed CLI) for private key retrieval.

**Spec:** [`docs/superpowers/specs/2026-05-03-gh-credential-routing-design.md`](../specs/2026-05-03-gh-credential-routing-design.md)

---

## Task 0: Preflight — gather installation IDs

**Files:** None (manual lookup, already resolved).

Both legion-implementer installation IDs are confirmed:
- `sjawhar` org install: **119465532**
- `trajectory-labs-pbc` org install: **119469290**

- [ ] **Step 1: Confirm the secrets store entry is reachable**

```bash
secrets get GH_AGENT_APP_PRIVATE_KEY_B64 | head -c 50
```

Expected: starts with `LS0t` (base64 prefix of `-----`). Confirms the key is present.

---

## Task 1: Add `useHttpPath` and profile sections to `.gitconfig`

**Files:**
- Modify: `~/.dotfiles/.gitconfig`

- [ ] **Step 1: Add `useHttpPath` and the two profile sections**

Open `~/.dotfiles/.gitconfig` and append:

```ini
[credential]
	useHttpPath = true

[gh-app "legion-personal"]
	app-id = 3202636
	installation-id = 119465532
	key-secret = GH_AGENT_APP_PRIVATE_KEY_B64

[gh-app "legion-work"]
	app-id = 3202636
	installation-id = 119469290
	key-secret = GH_AGENT_APP_PRIVATE_KEY_B64
```
Both installation IDs are concrete — no substitution needed.

- [ ] **Step 2: Verify git can read the profile data**

```bash
git config gh-app.legion-personal.app-id
git config gh-app.legion-personal.installation-id
git config gh-app.legion-personal.key-secret
git config gh-app.legion-work.installation-id
```

Expected output: `3202636`, `119465532`, `GH_AGENT_APP_PRIVATE_KEY_B64`, `119469290`.

- [ ] **Step 3: Verify `useHttpPath` is set**

```bash
git config credential.useHttpPath
```

Expected: `true`.

- [ ] **Step 4: Confirm interactive credential helper is unchanged**

```bash
git config --get-all credential.https://github.com.helper
```

Expected: two lines — empty, then `!gh auth git-credential`. (Same as before. We haven't touched the existing block.)

- [ ] **Step 5: Commit**

```bash
jj describe -m "feat(gitconfig): add useHttpPath and gh-app profile sections"
jj new
```

---

## Task 2: Write the routing smoke test (failing)

**Files:**
- Create: `~/.dotfiles/scripts/test-gh-routing`

This test exercises the agent-context routing matrix purely through `git config`, with no network calls. It is designed to be runnable from any shell (interactive OR agent) by explicitly clearing inherited `GIT_CONFIG_*` env vars before each assertion. It will fail at the end of this task because `gh-app-routes.gitconfig` doesn't exist yet — Task 3 makes it green.

- [ ] **Step 1: Create the test script**

```bash
cat > ~/.dotfiles/scripts/test-gh-routing <<'EOF'
#!/usr/bin/env bash
# Smoke test for per-repo GitHub credential routing.
#
# Asserts that with the gh-app-routes.gitconfig include active,
# `git config --get-urlmatch credential.helper <URL>` returns the
# expected helper for each test URL.
#
# Robust to being run from agent shells: explicitly clears any inherited
# GIT_CONFIG_* env vars before each git config call, then sets only the
# include we want to test.

set -euo pipefail

DOTFILES_DIR="${DOTFILES_DIR:-$HOME/.dotfiles}"
ROUTES_FILE="$DOTFILES_DIR/gh-app-routes.gitconfig"

pass=0
fail=0

# List GIT_CONFIG_* env var names currently set, for `env -u` scrubbing.
list_git_config_env() {
    compgen -e | grep '^GIT_CONFIG_' || true
}

# Build `env -u VAR1 -u VAR2 ...` arg list to scrub inherited GIT_CONFIG_*.
build_unset_args() {
    local v args=()
    while IFS= read -r v; do
        [[ -n "$v" ]] && args+=(-u "$v")
    done < <(list_git_config_env)
    printf '%s\n' "${args[@]}"
}

assert_helper() {
    local url="$1" expected="$2"
    local got
    # Run git config in a clean env: scrub leaked GIT_CONFIG_* from the parent,
    # then inject only our routes-file include.
    local -a unset_args=()
    while IFS= read -r v; do
        [[ -n "$v" ]] && unset_args+=(-u "$v")
    done < <(list_git_config_env)
    got=$(env "${unset_args[@]}" \
          GIT_CONFIG_COUNT=1 \
          GIT_CONFIG_KEY_0=include.path \
          GIT_CONFIG_VALUE_0="$ROUTES_FILE" \
          git config --get-urlmatch credential.helper "$url" 2>/dev/null \
          | tail -n 1) || got="<error>"
    if [[ "$got" == "$expected" ]]; then
        printf '  PASS  %-55s -> %s\n' "$url" "$got"
        ((pass++)) || true
    else
        printf '  FAIL  %s\n        expected: %s\n        got:      %s\n' \
               "$url" "$expected" "$got"
        ((fail++)) || true
    fi
}

echo "== gh credential routing assertions =="
assert_helper "https://github.com/sjawhar/dotfiles.git"          "!gh-app-token legion-personal"
assert_helper "https://github.com/trajectory-labs-pbc/agent-c.git" "!gh-app-token legion-work"
assert_helper "https://github.com/theorem-labs/whatever.git"     "!gh auth git-credential"

echo
echo "summary: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
EOF
chmod +x ~/.dotfiles/scripts/test-gh-routing
```

Notes on the design:
- `compgen -e | grep '^GIT_CONFIG_'` enumerates only `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*`, `GIT_CONFIG_COUNT` (NOT `GIT_CONFIG`, `GIT_CONFIG_GLOBAL`, etc., which use exact names — we don't touch those).
- `env -u <var> -u <var> ... GIT_CONFIG_COUNT=1 ... git ...` runs git in a child process with parent's `GIT_CONFIG_*` scrubbed and our test include set.
- `tail -n 1` selects the last (most-specific / final-after-resets) helper that `--get-urlmatch` outputs.

- [ ] **Step 2: Run the test, confirm all three assertions FAIL**

```bash
~/.dotfiles/scripts/test-gh-routing || true
```

Expected: all three URLs report FAIL because `gh-app-routes.gitconfig` doesn't exist yet — the include silently no-ops, so `git config --get-urlmatch` returns just the default `!gh auth git-credential` for the github.com host. The first two assertions expected `!gh-app-token ...`; they fail. The third (theorem-labs) expects `!gh auth git-credential` but ALSO sees only that, so it should PASS — actually it passes here. Let me restate:

- `sjawhar` URL: expected `!gh-app-token legion-personal`, got `!gh auth git-credential` → FAIL
- `trajectory-labs-pbc` URL: expected `!gh-app-token legion-work`, got `!gh auth git-credential` → FAIL
- `theorem-labs` URL: expected `!gh auth git-credential`, got `!gh auth git-credential` → PASS

Summary: `1 passed, 2 failed`. The script exits non-zero. This is the expected pre-Task-3 state.

- [ ] **Step 3: Commit the test**

```bash
jj describe -m "test: add gh routing smoke test"
jj new
```

---

## Task 3: Create `gh-app-routes.gitconfig`

**Files:**
- Create: `~/.dotfiles/gh-app-routes.gitconfig`

- [ ] **Step 1: Write the routes file**

```bash
cat > ~/.dotfiles/gh-app-routes.gitconfig <<'EOF'
# Per-org credential routes.
# This file is included into git config only by agent launchers (cc/oc),
# never by interactive shells. See docs/superpowers/specs/2026-05-03-gh-credential-routing-design.md.

[credential "https://github.com/sjawhar/"]
	helper =
	helper = !gh-app-token legion-personal

[credential "https://github.com/trajectory-labs-pbc/"]
	helper =
	helper = !gh-app-token legion-work

# Anything else: no entry here. Falls through to the default
# `!gh auth git-credential` configured in ~/.gitconfig.
EOF
```

- [ ] **Step 2: Re-run the routing smoke test**

```bash
~/.dotfiles/scripts/test-gh-routing
```

Expected: all `agent` cells now PASS. The script's exit code is 0.

- [ ] **Step 3: Confirm interactive shells are unchanged**

```bash
git config --get-urlmatch credential.helper https://github.com/sjawhar/dotfiles.git
```

Expected: `!gh auth git-credential` (the default — your interactive `git config` doesn't include the routes file).

- [ ] **Step 4: Commit**

```bash
jj describe -m "feat(gh-routing): add gh-app-routes.gitconfig with sjawhar and trajectory-labs-pbc routes"
jj new
```

---

## Task 4: Create `shims/gh-app-token` credential helper

**Files:**
- Create: `~/.dotfiles/shims/gh-app-token`

This ports the existing `shims/git-credential-gh-app` Python helper to take a profile name as `argv[1]` and read its config from `git config gh-app.<profile>.*` instead of env vars.

- [ ] **Step 1: Write the helper**

```bash
cat > ~/.dotfiles/shims/gh-app-token <<'EOF'
#!/usr/bin/env python3
"""Profile-aware GitHub App credential helper.

Used as a git credential helper:
  helper = !gh-app-token <profile>

The profile name is looked up in `git config`:
  [gh-app "<profile>"]
      app-id          = ...
      installation-id = ...
      key-secret      = NAME_OF_SECRET   ; resolved via `secrets get`

Generates an installation token, caches it per profile in
~/.cache/gh-app-tokens/<profile>.json, and emits a git-credential
formatted response on stdout.
"""

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "gh-app-tokens"
TOKEN_REFRESH_MARGIN = 300  # refresh 5 minutes before expiry


def git_config(key: str) -> str:
    """Read a single git config value or exit with a helpful message."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        print(f"gh-app-token: missing git config: {key}", file=sys.stderr)
        sys.exit(1)


def generate_jwt(app_id: str, private_key: str) -> str:
    import jwt
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(app_id: str, installation_id: str, private_key: str) -> dict:
    import urllib.request
    token = generate_jwt(app_id, private_key)
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_cached_token(profile: str) -> str | None:
    cache_file = CACHE_DIR / f"{profile}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        if (expires_at - datetime.now(timezone.utc)).total_seconds() > TOKEN_REFRESH_MARGIN:
            return data["token"]
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def cache_token(profile: str, token_data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{profile}.json"
    cache_file.write_text(json.dumps(token_data))
    cache_file.chmod(0o600)


def resolve_private_key(key_secret: str) -> str:
    try:
        b64_key = subprocess.run(
            ["secrets", "get", key_secret],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"gh-app-token: failed to fetch secret {key_secret}: {e}", file=sys.stderr)
        sys.exit(1)
    if not b64_key:
        print(f"gh-app-token: empty secret {key_secret}", file=sys.stderr)
        sys.exit(1)
    return base64.b64decode(b64_key).decode()


def get_token(profile: str) -> str:
    cached = get_cached_token(profile)
    if cached:
        return cached
    app_id = git_config(f"gh-app.{profile}.app-id")
    installation_id = git_config(f"gh-app.{profile}.installation-id")
    key_secret = git_config(f"gh-app.{profile}.key-secret")
    private_key = resolve_private_key(key_secret)
    print(f"gh-app-token: minting token for profile {profile}...", file=sys.stderr)
    token_data = get_installation_token(app_id, installation_id, private_key)
    cache_token(profile, token_data)
    return token_data["token"]


def main():
    if len(sys.argv) < 3:
        print("usage: gh-app-token <profile> <get|store|erase>", file=sys.stderr)
        sys.exit(1)
    profile = sys.argv[1]
    action = sys.argv[2]
    if action != "get":
        sys.exit(0)

    # Read the credential request (host, protocol, path) from stdin.
    request = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            break
        if "=" in line:
            key, _, value = line.partition("=")
            request[key] = value
    if "github.com" not in request.get("host", ""):
        sys.exit(0)

    token = get_token(profile)
    print(f"protocol={request.get('protocol', 'https')}")
    print(f"host={request.get('host', 'github.com')}")
    print("username=x-access-token")
    print(f"password={token}")


if __name__ == "__main__":
    main()
EOF
chmod +x ~/.dotfiles/shims/gh-app-token
```

- [ ] **Step 2: Verify the helper rejects missing profile**

```bash
printf 'protocol=https\nhost=github.com\npath=foo/bar.git\n\n' \
    | ~/.dotfiles/shims/gh-app-token nonexistent get
echo "exit: $?"
```

Expected: stderr says `gh-app-token: missing git config: gh-app.nonexistent.app-id`, exit code `1`. (The `host=github.com` in stdin is required — the helper silently no-ops if `host` isn't `github.com`, by design, so git credential pings to other hosts pass through cleanly. Using `</dev/null` would hit that short-circuit before the missing-config check fires.)

- [ ] **Step 3: Verify the helper mints a real token for `legion-personal`**

```bash
printf 'protocol=https\nhost=github.com\npath=sjawhar/dotfiles.git\n\n' \
    | ~/.dotfiles/shims/gh-app-token legion-personal get
```

Expected:
```
protocol=https
host=github.com
username=x-access-token
password=ghs_...
```

(stderr will print `gh-app-token: minting token for profile legion-personal...` on first call.)

- [ ] **Step 4: Verify the cached token is reused**

```bash
printf 'protocol=https\nhost=github.com\npath=sjawhar/dotfiles.git\n\n' \
    | ~/.dotfiles/shims/gh-app-token legion-personal get 2>&1 | grep -c minting
```

Expected: `0` (no minting message — the cached token from Step 3 is still valid).

- [ ] **Step 5: Verify token actually authenticates against GitHub**

```bash
token=$(printf 'protocol=https\nhost=github.com\npath=sjawhar/dotfiles.git\n\n' \
        | ~/.dotfiles/shims/gh-app-token legion-personal get \
        | sed -n 's/^password=//p')
curl -s -H "Authorization: token $token" -H "Accept: application/vnd.github+json" \
    https://api.github.com/installation/repositories | jq '.repositories[].full_name' | head
```

Expected: a list of repo names that this installation can access (should include `sjawhar/dotfiles` or similar).

- [ ] **Step 6: Verify `legion-work` profile mints a different token**

```bash
printf 'protocol=https\nhost=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n' \
    | ~/.dotfiles/shims/gh-app-token legion-work get | grep '^password='
```

Expected: a `password=ghs_...` line, distinct token from `legion-personal`.

- [ ] **Step 7: Commit**

```bash
jj describe -m "feat(gh-routing): add gh-app-token credential helper"
jj new
```

---

## Task 5: Verify end-to-end credential helper invocation via git

**Files:** None (verification only).

- [ ] **Step 1: Simulate agent context, run a credential lookup through git itself**

```bash
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=include.path \
GIT_CONFIG_VALUE_0="$HOME/.dotfiles/gh-app-routes.gitconfig" \
    git credential fill <<EOF
protocol=https
host=github.com
path=sjawhar/dotfiles.git
EOF
```

Expected output:
```
protocol=https
host=github.com
username=x-access-token
password=ghs_...
```

This proves git itself routes the credential lookup to `gh-app-token legion-personal` correctly when the include is active.

- [ ] **Step 2: Confirm interactive context bypasses the routing**

```bash
git credential fill <<EOF
protocol=https
host=github.com
path=sjawhar/dotfiles.git
EOF
```

Expected: the response comes from `gh auth git-credential` (your personal gh token, with `username=` your GitHub username, not `x-access-token`).

- [ ] **Step 3: No commit — this is verification only.** Move on.

---

## Task 6: Update `shims/gh` to drop `GH_APP` env detection and add route lookup

**Files:**
- Modify: `~/.dotfiles/shims/gh` (top of file: replace the "GitHub App identity" block).

The existing shim has this block near the top:

```bash
# --- GitHub App identity ---
if [[ -n "${GH_APP:-}" && -z "${GH_TOKEN:-}" ]]; then
    _gh_app_token=$(printf 'protocol=https\nhost=github.com\n\n' | git-credential-gh-app get 2>/dev/null) || true
    if [[ -n "$_gh_app_token" ]]; then
        GH_TOKEN=$(echo "$_gh_app_token" | grep '^password=' | cut -d= -f2-)
        export GH_TOKEN
    fi
    unset _gh_app_token
fi
```

Replace it with route-driven detection.

- [ ] **Step 1: Replace the GH_APP block with route-driven detection**

Open `~/.dotfiles/shims/gh` and replace the entire `# --- GitHub App identity ---` block (lines roughly matching the snippet above) with:

```bash
# --- GitHub App identity (route-driven) ---
# When invoked from a directory inside a git repo, look up the credential
# helper that would match the repo's remote URL. If it's a `!gh-app-token
# <profile>` helper, mint an installation token and export GH_TOKEN so
# subsequent gh API calls authenticate as that profile. In interactive
# shells (no agent include loaded) this finds the default user-helper
# and skips token export.
if [[ -z "${GH_TOKEN:-}" ]]; then
    _gh_remote_url=$(git remote get-url origin 2>/dev/null || true)
    if [[ -n "$_gh_remote_url" ]]; then
        # Normalize SSH form (git@host:owner/repo[.git]) to https URL.
        if [[ "$_gh_remote_url" =~ ^git@([^:]+):(.+)$ ]]; then
            _gh_remote_url="https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
        fi
        # Strip ssh:// prefix if present.
        _gh_remote_url="${_gh_remote_url/#ssh:\/\/git@/https://}"
        # Ensure trailing .git for stable matching (matches credential URL git uses).
        [[ "$_gh_remote_url" == *.git ]] || _gh_remote_url="${_gh_remote_url}.git"

        _gh_helper=$(git config --get-urlmatch credential.helper "$_gh_remote_url" 2>/dev/null || true)
        if [[ "$_gh_helper" == "!gh-app-token "* ]]; then
            _gh_profile="${_gh_helper#!gh-app-token }"
            # Build path for the credential request from the URL.
            _gh_path="${_gh_remote_url#https://*/}"
            _gh_token=$(printf 'protocol=https\nhost=github.com\npath=%s\n\n' "$_gh_path" \
                        | gh-app-token "$_gh_profile" get 2>/dev/null \
                        | sed -n 's/^password=//p') || true
            if [[ -n "$_gh_token" ]]; then
                export GH_TOKEN="$_gh_token"
            fi
            unset _gh_profile _gh_path _gh_token
        fi
        unset _gh_helper
    fi
    unset _gh_remote_url
fi
```

- [ ] **Step 2: Verify shim still works for non-routing cases**

From `$HOME` (no repo):

```bash
cd ~ && gh api user --jq .login
```

Expected: your GitHub username, no errors. (No remote = no routing = falls through to default `gh auth`.)

- [ ] **Step 3: Verify routing kicks in for an HTTPS app repo (agent context)**

```bash
cd ~/.dotfiles
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=include.path \
GIT_CONFIG_VALUE_0="$HOME/.dotfiles/gh-app-routes.gitconfig" \
    gh api /installation/repositories --jq '.repositories[].full_name' | head
```

Expected: a list of `sjawhar/...` repos (proves `GH_TOKEN` was set to a `legion-personal` installation token; the `/installation/repositories` endpoint returns the repos visible to whatever installation token is active). Note: `gh api app` does NOT work here — that endpoint requires a JWT, not an installation token.

- [ ] **Step 4: Verify routing kicks in for a different repo (different installation)**

```bash
cd ~/Code/agent-c
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=include.path \
GIT_CONFIG_VALUE_0="$HOME/.dotfiles/gh-app-routes.gitconfig" \
    gh api /installation/repositories --jq '.repositories[].full_name' | head
```

Expected: a list of `trajectory-labs-pbc/...` repos (proves `GH_TOKEN` was set to a `legion-work` installation token — same app slug, different installation, different repos).

- [ ] **Step 5: Verify SSH-remote repo still routes for gh CLI**

```bash
cd ~/Code/personal/theorem/default
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=include.path \
GIT_CONFIG_VALUE_0="$HOME/.dotfiles/gh-app-routes.gitconfig" \
    gh api user --jq .login
```

Expected: your username (not an app slug). The theorem-labs URL doesn't match any route; falls through to user gh auth — but the SSH-to-HTTPS normalization still ran, which is what we wanted to verify doesn't break.

- [ ] **Step 6: Confirm interactive shell behavior unchanged**

```bash
cd ~/.dotfiles
gh api user --jq .login
```

Expected: your username (no `GIT_CONFIG_*` env vars → no include → no routing → no `GH_TOKEN`).

- [ ] **Step 7: Commit**

```bash
jj describe -m "feat(gh-shim): replace GH_APP env detection with route-driven GH_TOKEN"
jj new
```

---

## Task 7: Update `scripts/cc` and `scripts/oc` to inject the include

**Files:**
- Modify: `~/.dotfiles/scripts/cc` (lines ~7–19 per the grep earlier)
- Modify: `~/.dotfiles/scripts/oc` (lines ~97–108)

- [ ] **Step 1: Read the current cc/oc to find the exact lines to replace**

```bash
grep -nE "GH_APP|GIT_CONFIG" ~/.dotfiles/scripts/cc
grep -nE "GH_APP|GIT_CONFIG" ~/.dotfiles/scripts/oc
```

Note the exact line numbers and surrounding context for each file. Both files have a near-identical block setting `GH_APP`, `GH_APP_ID`, `GH_APP_INSTALLATION_ID`, then a `GIT_CONFIG_COUNT/KEY/VALUE` block.

- [ ] **Step 2: In `scripts/cc`, replace the existing block**

Remove lines covering:
- `export GH_APP=legion-implementer`
- `export GH_APP_ID=3202636`
- `export GH_APP_INSTALLATION_ID=119469290`
- the existing `GIT_CONFIG_COUNT/KEY/VALUE` block that pointed `helper` at `gh-app`.

Replace with:

```bash
# Inject the gh-app routing config (per-repo credential routing for git+gh).
# Only loaded by agent shells; interactive shells are unaffected.
GIT_CONFIG_COUNT=$((${GIT_CONFIG_COUNT:-0} + 1))
_idx=$((GIT_CONFIG_COUNT - 1))
export GIT_CONFIG_COUNT
export "GIT_CONFIG_KEY_${_idx}"=include.path
export "GIT_CONFIG_VALUE_${_idx}"="${DOTFILES_DIR:-$HOME/.dotfiles}/gh-app-routes.gitconfig"
unset _idx
```

Preserve the surrounding comments that explain why credentials are being set up (rewording as needed: "agents authenticate via per-repo routing, not as your personal account").

- [ ] **Step 3: In `scripts/oc`, apply the same edit**

Identical block replacement.

- [ ] **Step 4: Verify cc launches without env-var errors**

```bash
bash -n ~/.dotfiles/scripts/cc
bash -n ~/.dotfiles/scripts/oc
```

Expected: both syntax-check clean (no output, exit 0).

- [ ] **Step 5: Verify routing fires when the cc/oc env is active**

Both `cc` and `oc` launch full agent UIs, so we verify the env-injection logic by reproducing it directly:

```bash
env $(env | grep -oE '^GIT_CONFIG_[A-Z_0-9]+|^GH_APP[A-Z_0-9]*|^GH_TOKEN' | sed 's/^/-u /' | tr '\n' ' ') \
    bash -c '
        cd ~/.dotfiles
        # Reproduce the post-Task-7 cc/oc env exactly
        GIT_CONFIG_COUNT=$((${GIT_CONFIG_COUNT:-0} + 1))
        _idx=$((GIT_CONFIG_COUNT - 1))
        export GIT_CONFIG_COUNT
        export "GIT_CONFIG_KEY_${_idx}"=include.path
        export "GIT_CONFIG_VALUE_${_idx}"="${DOTFILES_DIR:-$HOME/.dotfiles}/gh-app-routes.gitconfig"
        unset _idx
        gh api /installation/repositories --jq ".repositories[].full_name" | head -3
    '
```

Expected: a list of `sjawhar/...` repos (proves `GH_TOKEN` was set to `legion-personal` installation token via the post-Task-7 env). The leading `env -u ...` scrubs the agent shell's leaked env so we're testing the *new* injection logic in isolation.

- [ ] **Step 6: Re-run the full smoke test**

```bash
~/.dotfiles/scripts/test-gh-routing
```

Expected: all cells pass.

- [ ] **Step 7: Commit**

```bash
jj describe -m "feat(cc/oc): replace hardcoded GH_APP with gh-app-routes include"
jj new
```

---

## Task 8: Delete dead/obsolete code

**Files:**
- Delete: `~/.dotfiles/scripts/git-credential-gh` (dead code, no references)
- Delete: `~/.dotfiles/shims/git-credential-gh-app` (replaced by `gh-app-token`)

- [ ] **Step 1: Confirm `git-credential-gh-app` is no longer referenced**

```bash
grep -rn 'git-credential-gh-app' ~/.dotfiles/ \
    --include='*.sh' --include='*.py' --include='*.json' --include='*.toml' \
    --include='.gitconfig' --include='cc' --include='oc' 2>/dev/null \
    | grep -v -E '/transcripts/|/\.claude/plugins/marketplaces/'
```

Expected: empty (no live references). Transcript and marketplace mirror matches are historical and can be ignored.

- [ ] **Step 2: Confirm `git-credential-gh` is unreferenced**

```bash
grep -rn 'git-credential-gh\b' ~/.dotfiles/ \
    --include='*.sh' --include='*.py' --include='*.json' --include='*.toml' \
    --include='.gitconfig' --include='cc' --include='oc' 2>/dev/null \
    | grep -v 'git-credential-gh-app' \
    | grep -v -E '/transcripts/|/\.claude/plugins/marketplaces/'
```

Expected: empty.

- [ ] **Step 3: Delete the files**

```bash
rm ~/.dotfiles/scripts/git-credential-gh
rm ~/.dotfiles/shims/git-credential-gh-app
```

- [ ] **Step 4: Re-run smoke test to confirm nothing broke**

```bash
~/.dotfiles/scripts/test-gh-routing
```

Expected: all cells pass.

- [ ] **Step 5: Commit**

```bash
jj describe -m "chore: remove obsolete git-credential-gh and git-credential-gh-app shims"
jj new
```

---

## Task 9: Final end-to-end verification matrix

**Files:** None.

Verify the design's full §13 testing matrix manually in real sessions. This is the user-acceptance gate.

- [ ] **Step 1: Interactive shell, all three repos**

In your normal terminal (no `cc`/`oc`):

```bash
cd ~/.dotfiles                      && gh api user --jq .login
cd ~/Code/agent-c                   && gh api user --jq .login
cd ~/Code/personal/theorem/default  && gh api user --jq .login
```

Expected: all three return your GitHub username.

```bash
cd ~/.dotfiles && GIT_TRACE=1 git push --dry-run 2>&1 | grep credential
```

Expected: invokes `!gh auth git-credential` (the default).

- [ ] **Step 2: Agent shell (cc or oc), all three repos**

Launch a real agent session via `cc` or `oc`, and inside it run:

```bash
cd ~/.dotfiles                      && gh api app --jq .slug
cd ~/Code/agent-c                   && gh api app --jq .slug
cd ~/Code/personal/theorem/default  && gh api user --jq .login
```

Expected:
- First two: `legion-implementer` (different installations though — confirm via `gh api /installation/repositories --jq '.repositories[].full_name' | head`).
- Third (theorem-labs): your username (no route matches).

- [ ] **Step 3: Agent shell, real `git push --dry-run` over HTTPS in each repo**

Inside an agent session (cc/oc):

```bash
cd ~/.dotfiles                      && GIT_TRACE=1 git push --dry-run 2>&1 | grep -E 'credential|gh-app-token'
cd ~/Code/agent-c                   && GIT_TRACE=1 git push --dry-run 2>&1 | grep -E 'credential|gh-app-token'
cd ~/Code/personal/theorem/default  && GIT_TRACE=1 git push --dry-run 2>&1 | grep -E 'credential|gh|ssh'
```

Expected:
- `~/.dotfiles`: invokes `!gh-app-token legion-personal`.
- `~/Code/agent-c`: invokes `!gh-app-token legion-work`.
- `~/Code/personal/theorem/default`: uses SSH (no credential helper output; instead see ssh handshake).

- [ ] **Step 4: No commit needed (no code changes).** Plan complete.

---

## Self-Review Notes

- **Spec coverage:** Tasks 1–8 cover spec §4–§12. Task 9 implements §13 (testing matrix). Task 0 is the spec's implicit precondition (gather installation IDs).
- **Placeholders:** Task 0 leaves the trajectory-labs-pbc installation ID as a value to fill in, which is unavoidable user data — instruction is concrete on how to find it. No other placeholders.
- **Type/name consistency:** `gh-app.<profile>.{app-id,installation-id,key-secret}` used identically across Tasks 1, 4, 6. Profile names `legion-personal` and `legion-work` consistent across Tasks 1, 3, 4, 6, 7, 9. Routes file path `~/.dotfiles/gh-app-routes.gitconfig` consistent across Tasks 2, 3, 6, 7.
- **Out-of-scope (per spec §14):** branch-based routing, GHE hostnames, SSH replacement.
