# Design: per-repo GitHub credential routing for agents

**Date:** 2026-05-03
**Status:** Ready for review

## 1. Problem

The `cc` and `oc` launchers currently set a single hardcoded GitHub App identity (`legion-implementer`, with installation ID `119469290`) into the agent's environment via `GH_APP*` env vars and `GIT_CONFIG_*` injection. Every git push and `gh` CLI call from an agent uses that identity regardless of which repo is being touched.

This breaks down as soon as agents work in repos where that identity is wrong:

- `~/.dotfiles` (`sjawhar/dotfiles`) — wants the personal app installation in the `sjawhar` org.
- `~/Code/agent-c` (`trajectory-labs-pbc/agent-c`) — wants `legion-implementer` with the `trajectory-labs-pbc` installation (different installation ID from `sjawhar`).
- `~/Code/personal/theorem/default` (`theorem-labs/sami-worktrial-...`) — wants no app at all; `gh` CLI should use Sami's personal token.

The user's interactive shells should be unaffected.

## 2. Goal

When `cc`/`oc` launches an agent, every git push (HTTPS) and every `gh` CLI call should authenticate as the GitHub identity that matches the target repo, with no env-var hardcoding. Interactive (non-agent) shells continue to behave exactly as today.

## 3. Routing model

Routing is keyed off the **remote URL path** (`<host>:<owner>/<repo>`), with `protocol` available as a secondary signal. Both git's native credential matching and the `gh` shim use the same routing data.

Activation rules (per the user's specification):

- **Non-agent shell**: routing is inert. `git push` uses `gh auth git-credential` (the existing default); `gh` uses its built-in user auth.
- **Agent shell + git over SSH**: routing is bypassed by git itself (SSH never invokes credential helpers). `gh` CLI still routes (see below).
- **Agent shell + git over HTTPS**: routing applies. Owner-specific routes pick an app; everything unmatched falls through to `gh auth git-credential` (push as the user).
- **Agent shell + `gh` CLI**: routing always applies. Owner-specific routes mint an app installation token; everything unmatched leaves `GH_TOKEN` unset, so `gh` uses its default user auth.

Concrete routes (initial set):

| Pattern | Profile | Net effect |
|---|---|---|
| `github.com/sjawhar/*` | `legion-personal` | legion-implementer @ sjawhar installation |
| `github.com/trajectory-labs-pbc/*` | `legion-work` | legion-implementer @ trajectory-labs-pbc installation |
| (everything else) | (none) | falls through to `gh auth git-credential` / user gh token |

## 4. Single source of truth: `.gitconfig`

`~/.gitconfig` is symlinked into `~/.dotfiles` and committed. All routing data lives there, in two pieces:

1. **Profile definitions** (always present, inert until referenced) — define each `(app, installation)` pair plus a reference to the secret holding its private key. App IDs and installation IDs are not secrets and are safe to commit. The `key-secret` field holds the **name** of an entry in the user's `secrets` (sops) backend; the actual private key is decrypted on demand and never written to `.gitconfig`.

2. **Route entries** (`[credential "https://github.com/<org>/"]` sections) — live in a separate file `gh-app-routes.gitconfig`, also committed in dotfiles. This file is **only loaded** when an agent shell is launched, via `cc`/`oc` injecting `include.path = <routes-file>` through `GIT_CONFIG_*` env vars.

Splitting profiles from routes lets the profile data sit harmlessly in `.gitconfig` while the route entries (which would actively change git's behavior) only take effect for agent processes.

## 5. File layout

```
~/.dotfiles/
├── .gitconfig                      # MODIFIED: add useHttpPath; add [gh-app "..."] profile sections
├── gh-app-routes.gitconfig         # NEW: [credential "https://github.com/.../"] route sections
├── shims/
│   ├── gh-app-token                # NEW: profile-aware credential helper (Python)
│   ├── gh                          # MODIFIED: drop GH_APP env detection; route-driven GH_TOKEN
│   └── git-credential-gh-app       # DELETED — replaced by gh-app-token
└── scripts/
    ├── git-credential-gh           # DELETED — confirmed dead code, never referenced
    ├── cc                          # MODIFIED: drop GH_APP env block; inject include.path
    └── oc                          # MODIFIED: same
```

## 6. `.gitconfig` additions

```ini
[credential]
	useHttpPath = true                              # required for path-prefix matching

# Profile data — only references to secrets, never the secrets themselves.
[gh-app "legion-personal"]                          # legion-implementer @ sjawhar org
	app-id = 3202636
	installation-id = 119465532
	key-secret = GH_AGENT_APP_PRIVATE_KEY_B64       # name of entry in `secrets get`

[gh-app "legion-work"]                              # legion-implementer @ trajectory-labs-pbc
	app-id = 3202636
	installation-id = 119469290
	key-secret = GH_AGENT_APP_PRIVATE_KEY_B64       # same app, same key, different installation

# Future: a separate personal app would look like
# [gh-app "personal"]
#     app-id = ...
#     installation-id = ...
#     key-secret = GH_PERSONAL_APP_KEY_B64
```

The existing `[credential "https://github.com"]` block (with `helper = !gh auth git-credential`) is preserved unchanged. It is the default fallback for unmatched URLs.

## 7. `gh-app-routes.gitconfig` (new file)

```ini
[credential "https://github.com/sjawhar/"]
	helper =
	helper = !gh-app-token legion-personal

[credential "https://github.com/trajectory-labs-pbc/"]
	helper =
	helper = !gh-app-token legion-work

# Anything else: no entry; falls through to the default `!gh auth git-credential`
# already configured in .gitconfig.
```

The leading empty `helper =` clears any inherited helpers so only the app-token helper runs for a matched URL — preventing the default `gh auth git-credential` from also responding.

## 8. `shims/gh-app-token` (new helper)

Replaces the existing `shims/git-credential-gh-app`. Same internal logic for JWT generation and token caching, generalized to operate on a named profile rather than env vars.

Behaviour:
- Invoked as `gh-app-token <profile> get` (matches git credential helper protocol).
- Reads `gh-app.<profile>.app-id`, `installation-id`, `key-secret` via `git config`.
- Resolves the private key by `secrets get <key-secret>` and base64-decoding the result.
- Returns a cached installation token if one exists in `~/.cache/gh-app-tokens/<profile>.json` and is at least 5 minutes from expiry; otherwise mints a fresh one against the GitHub API and caches it. Cache key is the profile name (not the app slug) so multiple installations of the same app coexist correctly.
- Reads the credential request from stdin (host, protocol, path) and emits `username=x-access-token\npassword=<token>\n`.
- `store` and `erase` actions are no-ops; cache is managed internally.
- On any missing-config or secrets failure, exits non-zero. Because the route section in §7 cleared inherited helpers with `helper =`, git has no other helper to fall back to and reports an authentication error — a loud failure, not a silent fallback to user identity. (See §11.)

## 9. `shims/gh` rewrite

The current shim's `GH_APP`-from-env detection is replaced with a route lookup. The jj-bookmark logic (the bulk of the existing shim) is preserved unchanged.

```
1. Determine the target URL.
   - If invocation has -R owner/repo or a similar repo selector, build URL from that.
   - Otherwise: `git remote get-url origin` from PWD; if missing, no routing.
   - Normalize SSH form `git@github.com:foo/bar.git` to `https://github.com/foo/bar.git`
     for the `git config --get-urlmatch` lookup. (git push itself still uses SSH;
     the URL normalization is purely for routing the gh-side identity.)

2. helper=$(git config --get-urlmatch credential.helper "$url" 2>/dev/null || true)

3. If helper matches `!gh-app-token <profile>`:
     mint a token by piping a synthetic credential request into the helper
     (host=github.com, protocol=https, path=<owner>/<repo>.git);
     parse `password=<token>` from stdout;
     export GH_TOKEN=<token>.
   Otherwise: leave GH_TOKEN unset; gh uses its default auth.

4. Continue with existing jj-bookmark wrapping logic; exec real gh.
```

This means:

- In an interactive shell, `git config --get-urlmatch` only sees the default `!gh auth git-credential`, so the `if helper matches !gh-app-token` branch is never taken; `GH_TOKEN` is never set; `gh` runs as today.
- In an agent shell, the include is active, so `git config --get-urlmatch` returns the route-specific `!gh-app-token <profile>` for matched URLs. The shim mints the token and exports `GH_TOKEN`.

## 10. `cc` / `oc` simplification

Remove the existing block (paraphrased):

```bash
export GH_APP=legion-implementer
export GH_APP_ID=3202636
export GH_APP_INSTALLATION_ID=119469290
GIT_CONFIG_COUNT=...
GIT_CONFIG_KEY_X=credential.https://github.com.helper
GIT_CONFIG_VALUE_X=gh-app
```

Replace with a single include injection:

```bash
GIT_CONFIG_COUNT=$((${GIT_CONFIG_COUNT:-0} + 1))
i=$((GIT_CONFIG_COUNT - 1))
export GIT_CONFIG_KEY_$i=include.path
export GIT_CONFIG_VALUE_$i="$DOTFILES_DIR/gh-app-routes.gitconfig"
export GIT_CONFIG_COUNT
```

Both `cc` and `oc` apply this identical transformation. Both stop hardcoding any app identity.

## 11. Edge cases & error handling

**No remote configured / fresh `git init`.** `gh` shim's URL detection returns nothing → no `GH_TOKEN` → gh uses default auth. `git push` cannot proceed without a remote anyway.

**SSH remote.** `git push` uses SSH, bypassing credential helpers entirely. `gh` shim normalizes the SSH URL to HTTPS form for routing lookup; if a route matches, app token is exported; otherwise `gh` uses default auth. The user's SSH agent / keys are unaffected.

**Profile referenced but undefined in `.gitconfig`.** `gh-app-token` reads missing config keys → exits non-zero. Because the route section's `helper =` empty reset removed all inherited helpers, git treats the credential fetch as failed and reports an authentication error. This is correct: a misconfigured profile should produce a loud failure, not silently fall back to user identity (which would push agent commits under the wrong account).

**Secret unavailable from `secrets get`.** Same path as above — `gh-app-token` exits non-zero, git reports auth failure. The user fixes their secrets store.

**Token cache stale.** Existing 5-minute refresh margin is preserved.

**Multiple includes / order.** `cc`/`oc` append to existing `GIT_CONFIG_COUNT` rather than overwriting, so injection composes with other env-driven git config (e.g., per-test overrides).

**Interactive shell with `cc`/`oc` env vars leaked into it (e.g., user opens a new tmux pane that inherits the parent agent's env).** Routing is active in that shell. This is acceptable and matches the existing `GH_APP` env-var semantics — leaking agent env into interactive shells already has the same effect today. Not a regression.

## 12. Migration

- Delete `scripts/git-credential-gh` (dead code, no references).
- Delete `shims/git-credential-gh-app` once `gh-app-token` is in place and verified.
- Update `cc` and `oc` in lockstep with the new files.
- Add the second installation ID for `legion-work` to `.gitconfig` (user provides the value).
- The `secrets` entry `GH_AGENT_APP_PRIVATE_KEY_B64` already exists and is reused; no secrets-store changes needed for the initial rollout.

## 13. Testing

Manual verification matrix. For each cell, observe which identity authenticates:

| Repo | Agent context? | git push expected | gh CLI expected |
|---|---|---|---|
| `~/.dotfiles` | yes | `legion-personal` token | `legion-personal` token |
| `~/.dotfiles` | no | `gh auth` (Sami) | gh user token (Sami) |
| `~/Code/agent-c` | yes | `legion-work` token | `legion-work` token |
| `~/Code/agent-c` | no | `gh auth` (Sami) | gh user token (Sami) |
| `~/Code/personal/theorem/default` | yes | SSH (Sami) | gh user token (Sami) |
| `~/Code/personal/theorem/default` | no | SSH (Sami) | gh user token (Sami) |

Verification commands:

- `GIT_TRACE=1 git push --dry-run` — observe which credential helper git invokes for the matched URL.
- `gh api app --jq .slug` — when running with an app installation token, this returns the app slug (e.g. `legion-implementer`); with a user token it returns 404. Use this to confirm the identity is the one expected.
- `gh api repos/<owner>/<repo> --jq .full_name` — against the matching repo, returns 200 only if the active token has access; useful as a cross-check that the right installation was selected.
- A small smoke-test script that walks all six cells, runs the verifications, and reports pass/fail.

Smoke test script lives at `scripts/test-gh-routing` (created during implementation).

## 14. Out of scope

- **Branch-based routing.** The user noted "repo or branch" in the original ask; on inspection, branch is never available to the credential helper, and no concrete branch-vs-repo divergence exists in the three example cases. If a real need surfaces later, a per-clone override file (e.g., `.gh-app` in a worktree) can be added without redesigning the routing system.
- **Hostname routing beyond `github.com`.** Only github.com is in scope. GitHub Enterprise hosts can be added by extending the route patterns later.
- **Replacement for SSH-based pushing.** Users continue to use SSH where they have it set up; this design layers HTTPS routing alongside, not instead of, SSH.
