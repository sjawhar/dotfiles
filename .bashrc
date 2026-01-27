# Dotfiles shell configuration
DOTFILES_DIR="${DOTFILES_DIR:-${HOME}/.dotfiles}"

# ==============================================================================
# Shell detection (must be first - other sections depend on this)
# ==============================================================================

if [ -n "${ZSH_VERSION:-}" ]
then
    DOTFILES_SHELL="zsh"
else
    DOTFILES_SHELL="bash"
fi

# ==============================================================================
# Mise (tool version manager) - data stored in ~/.mise
# ==============================================================================

export MISE_DATA_DIR="${HOME}/.mise"

if [ -x "${DOTFILES_DIR}/bin/mise" ]
then
    eval "$("${DOTFILES_DIR}/bin/mise" activate --shims "$DOTFILES_SHELL")"
elif command -v mise &>/dev/null
then
    eval "$(mise activate --shims "$DOTFILES_SHELL")"
fi

# ==============================================================================
# Path additions
# ==============================================================================

# NPM global packages
export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
export PATH="${NPM_CONFIG_PREFIX}/bin:$PATH"

# ==============================================================================
# Completions
# ==============================================================================

# Load completions
for completion in "${DOTFILES_DIR}/completions.d"/*."${DOTFILES_SHELL}"; do
    [ -f "$completion" ] && . "$completion" || true
done

# ==============================================================================
# Starship prompt
# ==============================================================================

export STARSHIP_CONFIG="${DOTFILES_DIR}/starship.toml"
if command -v starship &>/dev/null
then
    eval "$(starship init "$DOTFILES_SHELL")"
fi

# ==============================================================================
# FZF (fuzzy finder)
# ==============================================================================

if command -v fzf &>/dev/null
then
    eval "$(fzf --"$DOTFILES_SHELL")"
fi

# ==============================================================================
# Zoxide (smart directory jumping)
# ==============================================================================

if command -v zoxide &>/dev/null
then
    eval "$(zoxide init "$DOTFILES_SHELL")"
fi

# ==============================================================================
# Tool configuration
# ==============================================================================

# jj (Jujutsu) - load config from both user-specific and shared dotfiles
export JJ_CONFIG="${HOME}/.config/jj/config.toml:${DOTFILES_DIR}/.jjconfig.toml"

# Claude Code
export CLAUDE_CONFIG_DIR="${DOTFILES_DIR}/.claude"

# ==============================================================================
# jj (Jujutsu) workspace utilities
# ==============================================================================

# Refresh all jj workspaces in current project
# Uses jj's workspace list and path lookup (requires workspaces to have recorded paths)
jj-refresh-workspaces() {
    local root_path ws path

    # 1. Get root workspace path
    root_path=$(jj workspace root 2>/dev/null) || return 1
    [ -f "$root_path/.jj/repo" ] && root_path="$(cat "$root_path/.jj/repo")" && root_path="${root_path%/.jj/repo}"

    # 2. Refresh root workspace
    echo "Refreshing workspace $root_path..."
    jj -R "$root_path" workspace update-stale &>/dev/null
    jj -R "$root_path" st &>/dev/null

    # 3. Refresh all other workspaces
    for ws in $(jj -R "$root_path" workspace list -T 'name ++ "\n"' --ignore-working-copy 2>/dev/null); do
        path=$(jj -R "$root_path" workspace root --name "$ws" 2>/dev/null) || continue
        echo "Refreshing workspace $path..."
        jj -R "$path" workspace update-stale &>/dev/null
        jj -R "$path" st &>/dev/null
    done
}

# ==============================================================================
# Modern CLI aliases
# ==============================================================================

# eza (modern ls with colors and git status)
if command -v eza &>/dev/null
then
    alias ls='eza'
    alias ll='eza -l'
    alias la='eza -la'
    alias lt='eza --tree'
fi

# bat (cat with syntax highlighting)
if command -v bat &>/dev/null
then
    alias cat='bat --paging=never'
fi

# ==============================================================================
# Claude Code aliases (Harper Reed workflow for mobile voice dictation)
# ==============================================================================

alias cc='claude'
alias ccd='claude --dangerously-skip-permissions'
alias ccc='claude --dangerously-skip-permissions --continue'

# ==============================================================================
# AWS profile management
# ==============================================================================

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

# AWS SSO login
alias alog='aws sso login --use-device-code'

# ==============================================================================
# Environment file loading
# ==============================================================================

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

# ==============================================================================
# Python development (UV-based)
# ==============================================================================

alias ulint='uv run ruff format && uv run ruff check --fix'
alias ucheck='uv run ruff format && uv run ruff check --fix && uv run basedpyright'

# ==============================================================================
# Tmux session management (Harper Reed workflow)
# ==============================================================================

# Attach to main session (creates if doesn't exist), like: mosh server -- tm
alias tm='tmux attach -t main || tmux new -s main'

# Proxy toggle (for Tailscale userspace networking)
alias proxy-on='. "${DOTFILES_DIR}/devpod/proxy.sh"'
alias proxy-off='unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy && echo "Proxy disabled"'

# ==============================================================================
# Language-specific environments
# ==============================================================================

# Rust/Cargo
[ ! -f "${HOME}/.cargo/env" ] || . "${HOME}/.cargo/env"

# ==============================================================================
# Kubernetes pod monitoring
# ==============================================================================

# Start ephemeral storage monitor (for k8s pods)
# Script handles single-instance via PID file
if [ -d /var/run/secrets/kubernetes.io/serviceaccount ]; then
  if [ -n "${DOTFILES_DIR:-}" ] && [ -x "${DOTFILES_DIR}/scripts/ephemeral-monitor" ]; then
    nohup "${DOTFILES_DIR}/scripts/ephemeral-monitor" >/dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
fi

# ==============================================================================
# Dotfiles paths (must be last to take precedence)
# shims/ contains wrapper scripts that intercept commands (e.g., pyright -> basedpyright)
# bin/ contains binaries and symlinks
# ==============================================================================

_path=""
IFS=':' read -ra _parts <<< "$PATH"
for _p in "${_parts[@]}"; do
    [[ "$_p" != "${DOTFILES_DIR}/bin" && "$_p" != "${DOTFILES_DIR}/shims" ]] && _path="${_path:+$_path:}$_p"
done
export PATH="${DOTFILES_DIR}/shims:${DOTFILES_DIR}/bin:$_path"
unset _path _parts _p
