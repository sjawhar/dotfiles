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
# Finds workspaces by locating directories whose .jj/repo points to the main repo
jj-refresh-workspaces() {
    local main_repo
    main_repo="$(jj workspace root 2>/dev/null)/.jj/repo" || return 1

    # Find directories with .jj/repo pointing to our main repo
    find ~ -maxdepth 3 -name ".jj" -type d 2>/dev/null | while read -r jj_dir; do
        local repo_file="$jj_dir/repo"
        [ -f "$repo_file" ] || continue
        local target
        target="$(cat "$repo_file" 2>/dev/null)"
        [ "$target" = "$main_repo" ] && echo "${jj_dir%/.jj}"
    done | sort -u | while read -r ws_path; do
        [ -z "$ws_path" ] && continue
        echo "Refreshing workspace $ws_path..."
        (cd "$ws_path" && jj workspace update-stale &>/dev/null; jj st &>/dev/null)
    done

    # Also update the main workspace itself
    local main_path="${main_repo%/.jj/repo}"
    echo "Refreshing workspace $main_path..."
    (cd "$main_path" && jj workspace update-stale &>/dev/null; jj st &>/dev/null)
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
alias cc-start='claude --dangerously-skip-permissions'
alias cc-continue='claude --dangerously-skip-permissions --continue'

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
