export PATH="${HOME}/.dotfiles/bin:$PATH"

if [ -d "${HOME}/.dotfiles/completions.d" ]; then
    while IFS= read -r completion; do
        . "$completion"
    done < <(find "${HOME}/.dotfiles/completions.d" -type f -name "*.bash")
fi

export STARSHIP_CONFIG="${HOME}/.dotfiles/starship.toml"
if command -v starship &> /dev/null; then
    eval "$(starship init bash)"
fi

export JJ_CONFIG="${HOME}/.config/jj/config.toml:${HOME}/.dotfiles/.jjconfig.toml"