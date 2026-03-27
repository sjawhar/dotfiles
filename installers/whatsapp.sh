#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Only install WhatsApp MCP daemon on the host node
if [ "${WHATSAPP_MCP_HOST:-false}" != "true" ]; then
    return 0
fi

# Create systemd user directory
mkdir -p ~/.config/systemd/user

# Symlink service file
ensure_link "${DOTFILES_DIR}/whatsapp/whatsapp-mcp.service" ~/.config/systemd/user/whatsapp-mcp.service

# Make wrapper script executable
chmod +x "${DOTFILES_DIR}/whatsapp/whatsapp-mcp-serve"

# Reload systemd and enable service (but don't start it yet)
systemctl --user daemon-reload
systemctl --user enable whatsapp-mcp 2>/dev/null || true

# Warn if sops age key is missing
if [ ! -f ~/.config/sops/age/keys.txt ]; then
    echo "WARNING: ~/.config/sops/age/keys.txt not found."
    echo "  The WhatsApp MCP daemon needs this to decrypt secrets."
    echo "  Write your age private key to this file."
fi
