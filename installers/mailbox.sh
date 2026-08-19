#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

mkdir -p ~/.config/mailbox
ensure_link "${DOTFILES_DIR}/mailbox.toml" ~/.config/mailbox/config.toml
chmod 600 "${DOTFILES_DIR}/mailbox.toml"

# Restart any live shell exporting these stale selectors before using mailbox >= 0.4.0.
for var in GWS_ACCOUNT MAILBOX_BROKER MAILBOX_SECRETS_REEXEC; do
    if grep -RIn --exclude-dir=.git "export ${var}=" "$DOTFILES_DIR" >/dev/null 2>&1; then
        echo "mailbox installer: stale export of ${var} found in dotfiles — remove it" >&2
        exit 1
    fi
done
