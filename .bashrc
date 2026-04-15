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

# ------------------------------------------------------------------------------
# Mise (tool version manager) - data stored in ~/.mise
# ------------------------------------------------------------------------------

export MISE_DATA_DIR="${HOME}/.mise"

if [ -x "${DOTFILES_DIR}/bin/mise" ]; then
    eval "$("${DOTFILES_DIR}/bin/mise" activate --shims "$DOTFILES_SHELL")"
elif command -v mise &>/dev/null; then
    eval "$(mise activate --shims "$DOTFILES_SHELL")"
fi

# OpenCode fork — auto-update checks our fork's releases
export OPENCODE_GITHUB_REPO="sjawhar/opencode"

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

# OpenCode
export PATH="${PATH}:${HOME}/.opencode/bin"

# Dotfiles paths (shims/ for wrappers, bin/ for binaries)
# Must be early to take precedence
_path=""
IFS=':' read -ra _parts <<< "$PATH"
for _p in "${_parts[@]}"; do
    [[ "$_p" != "${DOTFILES_DIR}/bin" && "$_p" != "${DOTFILES_DIR}/shims" ]] && _path="${_path:+$_path:}$_p"
done
export PATH="${DOTFILES_DIR}/shims:${DOTFILES_DIR}/bin:${DOTFILES_DIR}/scripts:$_path"
unset _path _parts _p

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

# Claude Code
export CLAUDE_CONFIG_DIR="${DOTFILES_DIR}/.claude"
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export ANTHROPIC_1M_CONTEXT=true


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
# Claude Code aliases (Harper Reed workflow for mobile voice dictation)
# ------------------------------------------------------------------------------

alias cc='claude'
alias ccd='claude --dangerously-skip-permissions'
alias ccc='claude --dangerously-skip-permissions --continue'

# ------------------------------------------------------------------------------
# OpenCode instance registry (oc, occ, oc ps)
# ------------------------------------------------------------------------------

occ() { oc --continue "$@"; }

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

# AWS SSO login (auto-copies device code to clipboard via OSC 52)
alog() {
    aws sso login --use-device-code 2>&1 | while IFS= read -r line; do
        printf '%s\n' "$line"
        if [[ "$line" =~ ([A-Z]{4}-[A-Z]{4}) ]]; then
            osc-copy "${BASH_REMATCH[1]}"
        fi
    done
}

# ------------------------------------------------------------------------------
# Oh My OpenCode config switching
# ------------------------------------------------------------------------------

# Usage: omo [profile]  — switch config profile (tab-completable)
#        omo             — show current profile and available profiles
omo() {
    local dotfiles="${DOTFILES_DIR:-${HOME}/.dotfiles}"
    local config="${HOME}/.config/opencode/oh-my-openagent.json"

    if [ -z "$1" ]; then
        # Show current profile
        local current="(unknown)"
        if [ -L "$config" ]; then
            local resolved
            resolved=$(readlink -f "$config")
            current=$(basename "$resolved" | sed 's/^oh-my-opencode\.//; s/\.json$//')
        elif [ -f "$config" ]; then
            current="(not a symlink)"
        fi
        echo "Current: $current"

        # List available profiles
        echo "Available profiles:"
        for f in "${dotfiles}"/opencode/oh-my-openagent.*.json; do
            [ -f "$f" ] || continue
            local name
            name=$(basename "$f" | sed 's/^oh-my-opencode\.//; s/\.json$//')
            if [ "$name" = "$current" ]; then
                echo "  $name (active)"
            else
                echo "  $name"
            fi
        done
        return
    fi

    local target="${dotfiles}/opencode/oh-my-openagent.${1}.json"
    if [ ! -f "$target" ]; then
        echo "Profile not found: $1" >&2
        echo "Run 'omo' to see available profiles." >&2
        return 1
    fi
    ln -sf "$target" "$config"
    echo "oh-my-opencode: switched to $1 (restart opencode to apply)"
}

# Tab completion for omo
if [ -n "${ZSH_VERSION:-}" ]; then
    _omo_complete() {
        local dotfiles="${DOTFILES_DIR:-${HOME}/.dotfiles}"
        local profiles=()
        for f in "${dotfiles}"/opencode/oh-my-openagent.*.json; do
            [ -f "$f" ] || continue
            profiles+=($(basename "$f" | sed 's/^oh-my-opencode\.//; s/\.json$//'))
        done
        _describe 'omo profile' profiles
    }
    compdef _omo_complete omo
else
    _omo_complete() {
        local dotfiles="${DOTFILES_DIR:-${HOME}/.dotfiles}"
        local profiles
        profiles=$(for f in "${dotfiles}"/opencode/oh-my-openagent.*.json; do
            [ -f "$f" ] || continue
            basename "$f" | sed 's/^oh-my-opencode\.//; s/\.json$//'
        done)
        COMPREPLY=($(compgen -W "$profiles" -- "${COMP_WORDS[COMP_CWORD]}"))
    }
    complete -F _omo_complete omo
fi

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

    # Process all workspaces
    jj -R "$root_path" workspace list -T 'name ++ "\n"' --ignore-working-copy 2>/dev/null | while IFS= read -r ws; do
        path=$(jj -R "$root_path" workspace root --name "$ws" 2>/dev/null) || path="$root_path"
        echo "Refreshing workspace $path..."
        jj -R "$path" workspace update-stale &>/dev/null || true
        jj -R "$path" st &>/dev/null
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

# Make a repo compatible with OpenCode by symlinking .opencode -> .claude
# and adding .opencode to the per-worktree git info/exclude
opencode-compat() {
    local ws_root
    ws_root=$(jj workspace root 2>/dev/null) || { echo "Error: not in a jj repo" >&2; return 1; }

    if [ ! -d "$ws_root/.claude" ]; then
        echo "Error: no .claude directory in $ws_root" >&2
        return 1
    fi

    # 1. Create .opencode -> .claude symlink
    if [ -L "$ws_root/.opencode" ]; then
        echo "Symlink already exists: $ws_root/.opencode -> $(readlink "$ws_root/.opencode")"
    elif [ -e "$ws_root/.opencode" ]; then
        echo "Error: $ws_root/.opencode exists but is not a symlink" >&2
        return 1
    else
        ln -s .claude "$ws_root/.opencode"
        echo "Created symlink: $ws_root/.opencode -> .claude"
    fi

    # 2. Add .opencode to per-worktree git info/exclude
    local gitdir
    if [ -f "$ws_root/.git" ]; then
        gitdir=$(sed 's/^gitdir: //' "$ws_root/.git")
        [[ "$gitdir" != /* ]] && gitdir="$ws_root/$gitdir"
    elif [ -d "$ws_root/.git" ]; then
        gitdir="$ws_root/.git"
    else
        echo "Error: no .git file or directory in $ws_root" >&2
        return 1
    fi

    local exclude="$gitdir/info/exclude"
    mkdir -p "$(dirname "$exclude")"
    if grep -qxF '.opencode' "$exclude" 2>/dev/null; then
        echo "Already in exclude: $exclude"
    else
        echo '.opencode' >> "$exclude"
        echo "Added .opencode to $exclude"
    fi
}

# Run opencode-compat across all jj workspaces in the current project
jj-opencode-compat() {
    local root_path ws path

    root_path=$(jj workspace root 2>/dev/null) || return 1
    [ -f "$root_path/.jj/repo" ] && root_path="$(cd "$root_path/.jj" && realpath "$(cat repo)")" && root_path="${root_path%/.jj/repo}"

    # Process root workspace first (has no recorded path)
    echo "=== default ($root_path) ==="
    (cd "$root_path" && opencode-compat)
    echo

    # Process all other workspaces
    for ws in $(jj -R "$root_path" workspace list -T 'name ++ "\n"' --ignore-working-copy 2>/dev/null); do
        path=$(jj -R "$root_path" workspace root --name "$ws" 2>/dev/null) || continue
        [ "$path" = "$root_path" ] && continue
        echo "=== $ws ($path) ==="
        (cd "$path" && opencode-compat)
        echo
    done
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
