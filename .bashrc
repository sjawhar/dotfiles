# Dotfiles shell configuration
DOTFILES_DIR="${DOTFILES_DIR:-${HOME}/.dotfiles}"
export PATH="${DOTFILES_DIR}/bin:$PATH"

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
# Mise (tool version manager)
# ==============================================================================

if command -v mise &>/dev/null
then
    eval "$(mise activate "$DOTFILES_SHELL")"
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
alias proxy-on='. ${DOTFILES_DIR}/devpod/proxy.sh'
alias proxy-off='unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy && echo "Proxy disabled"'

# ==============================================================================
# Language-specific environments
# ==============================================================================

# Rust/Cargo
[ ! -f "${HOME}/.cargo/env" ] || . "${HOME}/.cargo/env"
