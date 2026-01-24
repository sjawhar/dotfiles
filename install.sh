#!/bin/bash
set -eu

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_MISE=false

for arg in "$@"; do
    case $arg in
        --skip-mise) SKIP_MISE=true ;;
    esac
done

echo "=== Dotfiles Install ==="

# Shell config
if [ -f ~/.bashrc ]; then
    echo "" >> ~/.bashrc
    echo '[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"' >> ~/.bashrc
fi
. "${DOTFILES_DIR}/.bashrc"

# ------------------------------------------------------------------------------
# Mise (tool version manager)
# ------------------------------------------------------------------------------

mkdir -p ~/.config/mise
ln -sf "${DOTFILES_DIR}/mise.toml" ~/.config/mise/config.toml
mise trust ~/.config/mise/config.toml || echo "MISE INSTALL FAILED"

if ! command -v mise &>/dev/null
then
    echo "Installing mise..."
    curl -fsSL https://mise.run | MISE_INSTALL_PATH="${DOTFILES_DIR}/bin/mise" sh
    eval "$(mise activate "$DOTFILES_SHELL")"
fi

if [ "$SKIP_MISE" = true ]; then
    echo "Skipping mise install (--skip-mise)"
else
    echo "Installing tools via mise..."
    mise trust || echo "MISE TRUST FAILED"
    for i in {1..3}; do
        mise install && break || echo "Some tools failed to install (may be rate-limited). Run 'mise install' later."
        sleep 1
    done
fi

# ------------------------------------------------------------------------------
# Symlinks
# ------------------------------------------------------------------------------

echo "Creating symlinks..."
mkdir -p ~/.config/nvim
ln -sf "${DOTFILES_DIR}/.gitconfig" ~/.gitconfig
ln -sf "${DOTFILES_DIR}/.tmux.conf" ~/.tmux.conf
ln -sf "${DOTFILES_DIR}/nvim/init.lua" ~/.config/nvim/init.lua

# Install nvim plugins
if command -v nvim &>/dev/null; then
    echo "Installing nvim plugins..."
    nvim --headless "+Lazy! sync" +qa || echo "NVIM PLUGIN INSTALL FAILED"
fi

# ------------------------------------------------------------------------------
# jj (version control system)
# ------------------------------------------------------------------------------

# Shared config (aliases, editor, etc.) - lower priority
ln -sf "${DOTFILES_DIR}/.jjconfig.toml" ~/.jjconfig.toml

# Machine-specific config (user.name, user.email) - higher priority, overrides shared
mkdir -p ~/.config/jj
if [ ! -f ~/.config/jj/config.toml ]; then
    if [ -t 0 ]; then
        read -rp "Email for jj/git commits: " USER_EMAIL
        cat > ~/.config/jj/config.toml <<EOF
[user]
name = "Sami Jawhar"
email = "${USER_EMAIL}"
EOF
    else
        echo "Skipping jj config (non-interactive). Create ~/.config/jj/config.toml manually."
    fi
fi

# ------------------------------------------------------------------------------
# TPM (Tmux Plugin Manager)
# ------------------------------------------------------------------------------

if command -v tmux &>/dev/null; then
    TPM_DIR="${HOME}/.tmux/plugins/tpm"
    if [ ! -d "${TPM_DIR}/.git" ]; then
        echo "Installing TPM..."
        mkdir -p "${HOME}/.tmux/plugins"
        rm -rf "$TPM_DIR"
        git clone --depth 1 https://github.com/tmux-plugins/tpm "$TPM_DIR"
    fi
fi

# ------------------------------------------------------------------------------
# Claude Code
# ------------------------------------------------------------------------------

if ! command -v claude &>/dev/null; then
    echo "Installing Claude Code..."
    curl -fsSL https://claude.ai/install.sh | bash
fi

# ------------------------------------------------------------------------------
# Completions
# ------------------------------------------------------------------------------

echo "Generating completions..."
COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"
mkdir -p "$COMPLETIONS_DIR"

# Mise-managed tools (skip if not available)
jj util completion bash > "${COMPLETIONS_DIR}/jj.bash" || true
gh completion -s bash > "${COMPLETIONS_DIR}/gh.bash" || true

echo "=== Done! Restart your shell or run: source ~/.bashrc ==="
