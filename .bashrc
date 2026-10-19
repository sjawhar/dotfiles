# Dotfiles shell configuration
# Structure: Non-interactive safe stuff first, then interactive-only below the guard

DOTFILES_DIR="${DOTFILES_DIR:-${HOME}/.dotfiles}"

# ==============================================================================
# NON-INTERACTIVE SAFE SECTION
# Everything above the interactive guard runs for ALL shells (including
# non-interactive SSH commands like: ssh host 'some-command')
# ==============================================================================

# ------------------------------------------------------------------------------
# Shell detection (must be first - other sections depend on this)
# ------------------------------------------------------------------------------

if [ -n "${ZSH_VERSION:-}" ]; then
    DOTFILES_SHELL="zsh"
else
    DOTFILES_SHELL="bash"
fi

if [ -f "${DOTFILES_DIR}/dockerfunc" ]; then
    source "${DOTFILES_DIR}/dockerfunc"
fi

# systemd's per-user runtime dir. pam_systemd sets this for login sessions, but
# shells inside a tmux server that was itself started without it (e.g. by an
# agent over SSH) inherit the gap, which breaks systemctl --user and anything
# that looks for sockets there (forward's arming socket among them).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# ------------------------------------------------------------------------------
# Mise (tool version manager) - data stored in ~/.mise
# ------------------------------------------------------------------------------

export MISE_DATA_DIR="${HOME}/.mise"
# mise trusts its global config (this repo's mise.toml) and no other mise.toml,
# and an untrusted one holding [settings] or [tool_alias] fails to load. A
# clone's root mise.toml holds both, so without this every mise command in a
# clone, envoy/ included, fails until a `mise trust` or `mise install` run there
# trusts it. Trusting the two places this repo is checked out (this checkout,
# and the git clones agents work in) loads every mise.toml in them from the
# start. It is this variable, not mise.toml's trusted_config_paths, because a
# clone reads that setting as a project config's and warns on every mise
# command. omp's bash tool sets CI=true, under which mise trusts every file, so
# an untrusted one fails only in a human's shell.
export MISE_TRUSTED_CONFIG_PATHS="${DOTFILES_DIR}:${HOME}/.worktrees/dotfiles"

if [ -x "${DOTFILES_DIR}/bin/mise" ]; then
    eval "$("${DOTFILES_DIR}/bin/mise" activate --shims "$DOTFILES_SHELL")"
elif command -v mise &>/dev/null; then
    eval "$(mise activate --shims "$DOTFILES_SHELL")"
fi

# ------------------------------------------------------------------------------
# Tmux socket directory (keep out of /tmp to avoid systemd-tmpfiles cleanup)
# ------------------------------------------------------------------------------

export TMUX_TMPDIR="${HOME}/.tmux/sockets"
[ -d "$TMUX_TMPDIR" ] || mkdir -p "$TMUX_TMPDIR"

# ------------------------------------------------------------------------------
# Path additions
# ------------------------------------------------------------------------------

# NPM global packages
export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
export PATH="${NPM_CONFIG_PREFIX}/bin:$PATH"

# Dotfiles paths (shims/ for wrappers, bin/ for binaries)
# Must be early to take precedence
_path=""
IFS=':' read -ra _parts <<< "$PATH"
for _p in "${_parts[@]}"; do
    [[ "$_p" != "${DOTFILES_DIR}/bin" && "$_p" != "${DOTFILES_DIR}/shims" ]] && _path="${_path:+$_path:}$_p"
done
export PATH="${DOTFILES_DIR}/shims:${DOTFILES_DIR}/bin:${DOTFILES_DIR}/scripts:$_path"
unset _path _parts _p

# Authenticate mise's own GitHub API fallback (version/release lookups for
# every `github:` backend tool, including the private sjawhar/trajectory-labs
# repos mise's public versions-host mirror has no data for -- see
# mise.jdx.dev/getting-started.html#github-api-rate-limiting). Unauthenticated
# is 60/hour, shared by every session on this box; measured at 0/60 remaining
# while chasing this (AGENTC, 2026-10-05). gh-app-token already mints and
# caches this exact credential for git's own GitHub App routing above; reusing
# it here is the box's existing credential, not a new one. --owner sjawhar
# covers every `github:`-backed tool in mise.toml (all sjawhar/* or
# third-party public repos, which an authenticated token's higher bucket
# covers regardless of installation): never a pasted value, and silent on any
# failure (a locked keyring, no gh-app-token on PATH yet) the same way
# envoy-nats-seed is -- a shell with no token just falls back to the
# unauthenticated limit, exactly as before this existed.
if command -v gh-app-token &>/dev/null; then
    MISE_GITHUB_TOKEN="$(gh-app-token agent --owner sjawhar 2>/dev/null)" && export MISE_GITHUB_TOKEN
fi

# ------------------------------------------------------------------------------
# Language-specific environments
# ------------------------------------------------------------------------------

# Rust/Cargo
[ ! -f "${HOME}/.cargo/env" ] || . "${HOME}/.cargo/env"

# ------------------------------------------------------------------------------
# Tool configuration (environment variables)
# ------------------------------------------------------------------------------

# jj (Jujutsu) - load config from both user-specific and shared dotfiles
export JJ_CONFIG="${HOME}/.config/jj/config.toml:${DOTFILES_DIR}/.jjconfig.toml"

# Route xdg-open through forward to the laptop browser. The daemon pins an
# absolute opener path, so this is safe on both machines.
export BROWSER="${DOTFILES_DIR}/shims/xdg-open"


# Claude Code — state lives in ${DOTFILES_DIR}/.claude/; `cld --profile NAME` uses ${DOTFILES_DIR}/.claude-profiles/NAME.
# An inherited value is kept: Claude Code's Bash tool sources this file, and under `cld --profile NAME` its shells keep the profile's directory.
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${DOTFILES_DIR}/.claude}"
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export ANTHROPIC_1M_CONTEXT=true

# Playwright's `install` deletes every browser in ~/.cache/ms-playwright whose .links target it cannot
# read, and the cache is shared with every agent box, whose checkouts this shell cannot see (agentbox/AGENTS.md).
# Boxes get the same setting from scripts/agentbox's session_env.
export PLAYWRIGHT_SKIP_BROWSER_GC=1

# YubiKey PC/SC: forward serve owns ~/.pcscd/pcscd.comm on machines with the marker dir (devbox: forward's pcsc channel to the laptop; oryx: SSH-forwarded socat bridge).
if [ -d "${HOME}/.pcscd" ]; then
    export PCSCLITE_CSOCK_NAME="${HOME}/.pcscd/pcscd.comm"
fi

# Remote audio: forward serve owns $XDG_RUNTIME_DIR/forward/pulse.sock on serve-role
# machines (pulse channel to the laptop's pipewire-pulse). Explicit opt-in only —
# deliberately not PulseAudio's default path — so clients size buffers for a network
# round trip. When the channel is down, audio tools see a connection failure rather
# than "no audio system", which is the more honest error.
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -e "${HOME}/.config/systemd/user/forward-serve.service" ]; then
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/forward/pulse.sock"
fi

# Agent secrets (AGENTC-393). The broker's address: every machine carries it (the CLI's secret
# commands need it; the helper socket below exists only where the helper is installed); a value
# the caller already has wins.
if [ -z "${AGENT_SECRETS_URL:-}" ]; then
    . "${DOTFILES_DIR}/agent-secrets/broker.env"
    [ ! -f "${HOME}/.config/agent-secrets/env.local" ] || . "${HOME}/.config/agent-secrets/env.local"
fi
export AGENT_SECRETS_URL
# A host session gets its broker proofs from the agent-secrets helper over this socket
# (installers/agent-secrets.sh; the agent launchers register each session through
# scripts/agent-secrets-session). Agent boxes have a key dir instead: scripts/agentbox sets
# AGENT_SECRETS_KEY_DIR and never mounts this socket. Present only where the helper is installed,
# like PULSE_SERVER above; a value the caller already has wins.
if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -e "${HOME}/.config/systemd/user/agent-secrets-helper.service" ]; then
    export AGENT_SECRETS_HELPER_SOCK="${AGENT_SECRETS_HELPER_SOCK:-${XDG_RUNTIME_DIR}/agent-secrets/helper.sock}"
fi


# ==============================================================================
# INTERACTIVE GUARD
# Everything below this point only runs for interactive shells
# ==============================================================================

[[ $- == *i* ]] || return 0

# Re-activate mise with hooks for interactive shells — ensures upgrades
# take effect without needing a new shell (--shims above handles non-interactive)
if [ -x "${DOTFILES_DIR}/bin/mise" ]; then
    eval "$("${DOTFILES_DIR}/bin/mise" activate "$DOTFILES_SHELL")"
elif command -v mise &>/dev/null; then
    eval "$(mise activate "$DOTFILES_SHELL")"
fi

# Mise activation registers a PROMPT_COMMAND hook that re-prepends its tool
# install dirs to PATH every prompt, which would shadow our shims (e.g. the
# gh wrapper that sets up GitHub App auth). Append our own hook so shims
# always end up first, regardless of what mise just did.
_ensure_dotfiles_shims_first() {
    local p _newpath="" _parts
    IFS=':' read -ra _parts <<< "$PATH"
    for p in "${_parts[@]}"; do
        [[ "$p" != "${DOTFILES_DIR}/shims" ]] && _newpath="${_newpath:+$_newpath:}$p"
    done
    export PATH="${DOTFILES_DIR}/shims:$_newpath"
}
_ensure_dotfiles_shims_first
PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND$'\n'}_ensure_dotfiles_shims_first"

# ==============================================================================
# INTERACTIVE-ONLY SECTION
# Completions, prompts, aliases, functions - things that need a terminal
# ==============================================================================

# ------------------------------------------------------------------------------
# Completions
# ------------------------------------------------------------------------------

for completion in "${DOTFILES_DIR}/completions.d"/*."${DOTFILES_SHELL}"; do
    [ -f "$completion" ] && . "$completion" || true
done

# ------------------------------------------------------------------------------
# Starship prompt
# ------------------------------------------------------------------------------

export STARSHIP_CONFIG="${DOTFILES_DIR}/starship.toml"
if command -v starship &>/dev/null; then
    eval "$(starship init "$DOTFILES_SHELL")"
fi

# ------------------------------------------------------------------------------
# FZF (fuzzy finder)
# ------------------------------------------------------------------------------

if command -v fzf &>/dev/null; then
    eval "$(fzf --"$DOTFILES_SHELL")"
fi

# ------------------------------------------------------------------------------
# Zoxide (smart directory jumping)
# ------------------------------------------------------------------------------

if command -v zoxide &>/dev/null; then
    eval "$(zoxide init "$DOTFILES_SHELL")"
fi

# ------------------------------------------------------------------------------
# Modern CLI aliases
# ------------------------------------------------------------------------------

# eza (modern ls with colors and git status)
if command -v eza &>/dev/null; then
    alias ls='eza'
    alias ll='eza -l'
    alias la='eza -la'
    alias lt='eza --tree'
fi

# bat (cat with syntax highlighting)
if command -v bat &>/dev/null; then
    alias cat='bat --paging=never'
fi

# ------------------------------------------------------------------------------
# Python development (UV-based)
# ------------------------------------------------------------------------------

alias ulint='uv run ruff format && uv run ruff check --fix'
alias ucheck='uv run ruff format && uv run ruff check --fix && uv run basedpyright'

# ------------------------------------------------------------------------------
# Tmux session management (Harper Reed workflow)
# ------------------------------------------------------------------------------

# Attach to main session (creates if doesn't exist), like: mosh server -- tm
alias tm='tmux attach -t main || tmux new -s main'
alias s='secrets'

# Proxy toggle (for Tailscale userspace networking)
alias proxy-on='. "${DOTFILES_DIR}/devpod/proxy.sh"'
alias proxy-off='unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy && echo "Proxy disabled"'

# ------------------------------------------------------------------------------
# AWS profile management
# ------------------------------------------------------------------------------

# AWS profile switcher with tab completion
aprof() {
    if [ -z "$1" ]; then
        echo "Current: ${AWS_PROFILE:-<not set>}"
        echo "Available profiles:"
        grep '^\[profile ' ~/.aws/config 2>/dev/null | sed 's/\[profile \(.*\)\]/  \1/'
        return
    fi
    export AWS_PROFILE="$1"
    echo "AWS_PROFILE=$AWS_PROFILE"
}

# Tab completion for aprof (bash and zsh)
if [ -n "${ZSH_VERSION:-}" ]; then
    _aprof_complete() {
        local profiles
        profiles=($(grep '^\[profile ' ~/.aws/config 2>/dev/null | sed 's/\[profile \(.*\)\]/\1/'))
        _describe 'AWS profile' profiles
    }
    compdef _aprof_complete aprof
else
    _aprof_complete() {
        local profiles
        profiles=$(grep '^\[profile ' ~/.aws/config 2>/dev/null | sed 's/\[profile \(.*\)\]/\1/')
        COMPREPLY=($(compgen -W "$profiles" -- "${COMP_WORDS[COMP_CWORD]}"))
    }
    complete -F _aprof_complete aprof
fi

# Copy text to system clipboard via OSC 52 (works through SSH + tmux)
osc-copy() {
    local seq
    seq=$(printf '\033]52;c;%s\a' "$(printf '%s' "$1" | base64)")
    [ -n "$TMUX" ] && seq=$(printf '\033Ptmux;\033%s\033\\' "$seq")
    printf '%s' "$seq" > /dev/tty
}

# AWS SSO login: device-code flow opens the pre-filled code on the laptop;
# any printed code is still OSC 52 copied as the tunnel-down fallback.
alog() {
    aws sso login --use-device-code "$@" 2>&1 | while IFS= read -r line; do
        printf '%s\n' "$line"
        if [[ "$line" =~ ([A-Z]{4}-[A-Z]{4}) ]]; then
            osc-copy "${BASH_REMATCH[1]}"
        fi
    done
}

# ------------------------------------------------------------------------------
# GNOME keyring / Secret Service unlock (headless boxes)
# ------------------------------------------------------------------------------

# On a headless SSH box there is no GUI prompter and PAM never receives a login
# password (public-key auth), so the Secret Service "login" keyring loads locked
# and tools like `pup` fail with "result not returned from SS API". This unlocks
# it with a real password you type: the first run creates the keyring encrypted
# with that password, later runs just unlock it. Linger is on, so you unlock
# once per boot and it stays unlocked for every SSH session until reboot.
# (An already-running daemon can't be unlocked via the CLI, so we kill+restart.)
unlock-keyring() {
    local pw
    read -rsp 'Keyring password: ' pw; echo
    killall -q gnome-keyring-daemon 2>/dev/null
    sleep 0.3
    eval "$(printf '%s' "$pw" | gnome-keyring-daemon --replace --daemonize --components=secrets --unlock 2>/dev/null)"
    unset pw
    if printf 'x' | secret-tool store --label=_probe _keyring_probe 1 >/dev/null 2>&1; then
        secret-tool clear _keyring_probe 1 >/dev/null 2>&1
        echo 'keyring unlocked'
    else
        echo 'keyring still locked — wrong password? re-run unlock-keyring' >&2
        return 1
    fi
}

# ------------------------------------------------------------------------------
# Environment file loading
# ------------------------------------------------------------------------------

# Load .env file (defaults to .env in current directory)
loadenv() {
    local envfile="${1:-.env}"
    if [ -f "$envfile" ]; then
        set -a
        source "$envfile"
        set +a
        echo "Loaded $envfile"
    else
        echo "File not found: $envfile" >&2
        return 1
    fi
}

# ------------------------------------------------------------------------------
# File concatenation utility
# ------------------------------------------------------------------------------

# Concatenate files with path headers and separators
# Usage: codecat [files/globs...] > output.txt
codecat() {
    local first=1
    for file in "$@"; do
        [ -f "$file" ] || continue
        [ "$first" -eq 1 ] || echo "---"
        first=0
        echo "# $file"
        cat "$file"
    done
}

# ------------------------------------------------------------------------------
# jj (Jujutsu) workspace utilities
# ------------------------------------------------------------------------------

# Refresh all jj workspaces (update-stale + status snapshot)
# With --rebase: also fetch and rebase each workspace onto a base branch
# Usage: jj-refresh [--rebase [BRANCH]]
jj-refresh() {
    local rebase=false base_branch="main" root_path ws path

    # Parse args
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rebase)
                rebase=true; shift
                if [[ $# -gt 0 && "$1" != -* ]]; then
                    base_branch="$1"; shift
                fi
                ;;
            *) echo "Usage: jj-refresh [--rebase [BRANCH]]" >&2; return 1 ;;
        esac
    done

    # Get root workspace path
    root_path=$(jj workspace root 2>/dev/null) || return 1
    [ -f "$root_path/.jj/repo" ] && root_path="$(cd "$root_path/.jj" && realpath "$(cat repo)")" && root_path="${root_path%/.jj/repo}"

    # Fetch if rebasing
    if [ "$rebase" = true ]; then
        jj -R "$root_path" git fetch --remote origin --branch "$base_branch" || return 1
    fi

    # Process all workspaces. `default` has no recorded path (it is the root);
    # any other workspace whose path cannot be resolved is reported and skipped,
    # never silently refreshed as if it were the root.
    jj -R "$root_path" workspace list -T 'name ++ "\n"' --ignore-working-copy 2>/dev/null | while IFS= read -r ws; do
        if [ "$ws" = default ]; then
            path="$root_path"
        elif ! path=$(jj -R "$root_path" --ignore-working-copy workspace root --name "$ws" 2>&1); then
            echo "Skipping workspace $ws: ${path#Error: }" >&2
            continue
        fi
        echo "Refreshing workspace $path..."
        # Snapshot; recover only when jj itself says the working copy is stale.
        # Any other failure is shown, not swallowed.
        if ! err=$(jj -R "$path" st 2>&1 >/dev/null); then
            if [[ "$err" == *"working copy is stale"* ]]; then
                jj -R "$path" workspace update-stale >/dev/null
            else
                echo "$err" >&2
            fi
        fi
        if [ "$rebase" = true ]; then
            echo "  Rebasing onto $base_branch..."
            (cd "$path" && jj rebase -d "$base_branch")
        fi
    done
}

# Sync a bookmark from upstream to origin and push tags
jj-sync-upstream() {
    local bookmark="$1"

    # Verify upstream remote exists
    jj git remote list 2>/dev/null | grep -q '^upstream ' || {
        echo "Error: no 'upstream' remote configured" >&2
        return 1
    }

    # Auto-detect bookmark: explicit arg > dev@upstream > main
    if [ -z "$bookmark" ]; then
        if jj log -r 'dev@upstream' --no-graph --limit 1 &>/dev/null; then
            bookmark="dev"
        else
            bookmark="main"
        fi
    fi

    jj git fetch --all-remotes || return 1
    jj bookmark set "$bookmark" --to "${bookmark}@upstream" || return 1
    jj git push --bookmark "$bookmark" || return 1
    git push --tags
}

# ------------------------------------------------------------------------------
# Security: idle shell timeout (skip inside tmux)
# ------------------------------------------------------------------------------

if [ -n "${SSH_CONNECTION:-}" ] && [ -z "${TMUX:-}" ]; then
    TMOUT=900
fi

# ------------------------------------------------------------------------------
# Security: reboot-required notification
# ------------------------------------------------------------------------------

if [ -f /var/run/reboot-required ]; then
    printf '\033[1;33m*** System restart required ***\033[0m\n'
    if [ -f /var/run/reboot-required.pkgs ]; then
        cat /var/run/reboot-required.pkgs | sed 's/^/    /'
    fi
fi

# ------------------------------------------------------------------------------
# Kubernetes pod monitoring
# ------------------------------------------------------------------------------

# Start ephemeral storage monitor (for k8s pods)
# Script handles single-instance via PID file
if [ -d /var/run/secrets/kubernetes.io/serviceaccount ]; then
    if [ -n "${DOTFILES_DIR:-}" ] && [ -x "${DOTFILES_DIR}/scripts/ephemeral-monitor" ]; then
        nohup "${DOTFILES_DIR}/scripts/ephemeral-monitor" >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
fi
