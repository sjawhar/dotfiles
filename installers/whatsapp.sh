#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
MISE="${DOTFILES_DIR}/bin/mise"

# Role-specific: only the machine holding the paired WhatsApp session runs the
# daemon, so machine installers invoke this explicitly (laptop/install.sh);
# root install.sh does not.
if ! "$MISE" which whatsapp-mcp >/dev/null 2>&1; then
    "$MISE" install "npm:@sjawhar/whatsapp-mcp"
fi

mkdir -p "${HOME}/.config/systemd/user"
ln -sfn "${DOTFILES_DIR}/whatsapp/whatsapp-mcp.service" "${HOME}/.config/systemd/user/whatsapp-mcp.service"

systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload whatsapp-mcp (no user systemd session here?) — reload it on the target machine."
systemctl --user enable --now whatsapp-mcp 2>/dev/null \
    || echo "NOTE: could not enable whatsapp-mcp (no user systemd session here?) — enable it on the target machine."
systemctl --user try-restart whatsapp-mcp 2>/dev/null \
    || echo "NOTE: could not restart whatsapp-mcp (no user systemd session here?) — restart it on the target machine."

# The daemon listens on loopback only, so its MCP client belongs to this
# machine, not to the shared omp/mcp.json (where it would fail to connect in
# every session on every other machine). omp reads ~/.omp/agent/.mcp.json as a
# second user-level config next to the symlinked mcp.json; it is machine-local.
OMP_LOCAL_MCP="${HOME}/.omp/agent/.mcp.json"
mkdir -p "$(dirname "$OMP_LOCAL_MCP")"
[ -f "$OMP_LOCAL_MCP" ] || echo '{"mcpServers":{}}' > "$OMP_LOCAL_MCP"
ensure_json "$OMP_LOCAL_MCP" \
    '.mcpServers.whatsapp.args == ["WHATSAPP_MCP_URL","WHATSAPP_MCP_SECRET","--","whatsapp-mcp","--connect"]' \
    '.mcpServers.whatsapp = {"command":"secrets","args":["WHATSAPP_MCP_URL","WHATSAPP_MCP_SECRET","--","whatsapp-mcp","--connect"]}' \
    "Registering the whatsapp MCP client in ${OMP_LOCAL_MCP}"

echo "WhatsApp MCP daemon installed. Until a session is paired, it prints a QR code to its journal:"
echo "  journalctl --user -u whatsapp-mcp -o cat -f    (WhatsApp > Linked devices > Link a device)"
