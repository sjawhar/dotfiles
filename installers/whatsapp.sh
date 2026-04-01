#!/bin/bash
# WhatsApp MCP installer — delegates to whatsapp-mcp/deploy/install.sh
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WHATSAPP_MCP_DIR="${HOME}/Code/whatsapp-mcp"

if [ -d "$WHATSAPP_MCP_DIR/deploy" ]; then
    source "$WHATSAPP_MCP_DIR/deploy/install.sh"
else
    echo "ERROR: WhatsApp MCP deploy directory not found at $WHATSAPP_MCP_DIR/deploy" >&2
    return 1
fi
