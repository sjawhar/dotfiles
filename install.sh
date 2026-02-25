#!/bin/bash
set -euo pipefail
export DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dotfiles Install ==="

source "${DOTFILES_DIR}/installers/shell.sh"
source "${DOTFILES_DIR}/installers/mise.sh" "$@"
source "${DOTFILES_DIR}/installers/sops.sh"
source "${DOTFILES_DIR}/installers/jj.sh"
source "${DOTFILES_DIR}/installers/tmux.sh"
source "${DOTFILES_DIR}/installers/nvim.sh"
source "${DOTFILES_DIR}/installers/claude.sh"
source "${DOTFILES_DIR}/installers/opencode.sh"

echo "Generating completions..."
COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"
mkdir -p "$COMPLETIONS_DIR"
timeout 5 jj util completion bash > "${COMPLETIONS_DIR}/jj.bash" 2>/dev/null || true
timeout 5 gh completion -s bash > "${COMPLETIONS_DIR}/gh.bash" 2>/dev/null || true

echo "=== Done! Restart your shell or run: source ~/.bashrc ==="
