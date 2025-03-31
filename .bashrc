PS1="${PS1/'\[\033[00m\]\$'/'\[\033[36m\]`__git_ps1`\[\033[00m\]\n\$'}"

export PATH="${HOME}/.jj/bin:$PATH"

if [ -d ~/.dotfiles/completions.d ]; then
    for completion in ~/.dotfiles/completions.d/*.bash; do
        . "$completion"
    done
fi
