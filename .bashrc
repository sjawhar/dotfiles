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

# ------------------------------------------------------------------------------
# Mise (tool version manager) - data stored in ~/.mise
# ------------------------------------------------------------------------------

export MISE_DATA_DIR="${HOME}/.mise"

if [ -x "${DOTFILES_DIR}/bin/mise" ]; then
    eval "$("${DOTFILES_DIR}/bin/mise" activate --shims "$DOTFILES_SHELL")"
elif command -v mise &>/dev/null; then
    eval "$(mise activate --shims "$DOTFILES_SHELL")"
fi

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
export PATH="${DOTFILES_DIR}/shims:${DOTFILES_DIR}/bin:$_path"
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
# OpenCode aliases
# ------------------------------------------------------------------------------

alias oc='opencode'
alias occ='opencode --continue'

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
    local config="${HOME}/.config/opencode/oh-my-opencode.json"

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
        for f in "${dotfiles}"/oh-my-opencode.*.json; do
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

    local target="${dotfiles}/oh-my-opencode.${1}.json"
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
        for f in "${dotfiles}"/oh-my-opencode.*.json; do
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
        profiles=$(for f in "${dotfiles}"/oh-my-opencode.*.json; do
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

# Refresh all jj workspaces in current project
# Uses jj's workspace list and path lookup (requires workspaces to have recorded paths)
jj-refresh-workspaces() {
    local root_path ws path

    # 1. Get root workspace path
    root_path=$(jj workspace root 2>/dev/null) || return 1
    [ -f "$root_path/.jj/repo" ] && root_path="$(cd "$root_path/.jj" && realpath "$(cat repo)")" && root_path="${root_path%/.jj/repo}"

    # 2. Refresh root workspace
    echo "Refreshing workspace $root_path..."
    jj -R "$root_path" workspace update-stale &>/dev/null
    jj -R "$root_path" st &>/dev/null

    # 3. Refresh all other workspaces
    for ws in $(jj -R "$root_path" workspace list -T 'name ++ "\n"' --ignore-working-copy 2>/dev/null); do
        path=$(jj -R "$root_path" workspace root --name "$ws" 2>/dev/null) || continue
        [ "$path" = "$root_path" ] && continue
        echo "Refreshing workspace $path..."
        jj -R "$path" workspace update-stale &>/dev/null
        jj -R "$path" st &>/dev/null
    done
}

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
