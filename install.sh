#!/bin/bash
set -euo pipefail
DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOTFILES_DIR

echo "=== Dotfiles Install ==="

source "${DOTFILES_DIR}/installers/shell.sh"
source "${DOTFILES_DIR}/installers/mailbox.sh"
source "${DOTFILES_DIR}/installers/gws.sh"
source "${DOTFILES_DIR}/installers/mise.sh" "$@"
source "${DOTFILES_DIR}/installers/agent-browser.sh"
source "${DOTFILES_DIR}/installers/docker.sh"
source "${DOTFILES_DIR}/installers/sops.sh"
source "${DOTFILES_DIR}/installers/jj.sh"
source "${DOTFILES_DIR}/installers/tmux.sh"
source "${DOTFILES_DIR}/installers/nvim.sh"
source "${DOTFILES_DIR}/installers/claude.sh"
source "${DOTFILES_DIR}/installers/omp.sh"
source "${DOTFILES_DIR}/installers/secretsd.sh"
# The secrets broker's host helper runs only where env.local names a broker; run it as a command,
# not source, because it and agent-secrets.sh can exit early.
bash "${DOTFILES_DIR}/installers/agent-secrets-local.sh"
source "${DOTFILES_DIR}/installers/agent-spend.sh"
source "${DOTFILES_DIR}/installers/ctx-memory.sh"

echo "Generating completions..."
COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"
mkdir -p "$COMPLETIONS_DIR"
timeout 5 jj util completion bash > "${COMPLETIONS_DIR}/jj.bash" 2>/dev/null || true
timeout 5 gh completion -s bash > "${COMPLETIONS_DIR}/gh.bash" 2>/dev/null || true

# Last: it joins this machine to the shared knives ledger, and a sweep that
# would strand entries written here stops the install.
source "${DOTFILES_DIR}/installers/knives.sh"

echo "=== Done! Restart your shell or run: source ~/.bashrc ==="
