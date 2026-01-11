export PATH="${HOME}/.dotfiles/bin:$PATH"

export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
export PATH="${NPM_CONFIG_PREFIX}/bin:$PATH"

# Detect current shell (use ${VAR:-} to handle set -u)
if [ -n "${ZSH_VERSION:-}" ]; then
    DOTFILES_SHELL="zsh"
else
    DOTFILES_SHELL="bash"
fi

# Load completions (with error handling)
for completion in "${HOME}/.dotfiles/completions.d"/*."${DOTFILES_SHELL}"; do
    [ -f "$completion" ] && . "$completion" || true
done

export STARSHIP_CONFIG="${HOME}/.dotfiles/starship.toml"
if command -v starship &> /dev/null; then
    eval "$(starship init "$DOTFILES_SHELL")"
fi

export JJ_CONFIG="${HOME}/.config/jj/config.toml:${HOME}/.dotfiles/.jjconfig.toml"

export CLAUDE_CONFIG_DIR="${HOME}/.dotfiles/.claude"
