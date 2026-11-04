#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# A Google VM uses its real metadata server. No environment variable is changed.
if "${DOTFILES_DIR}/scripts/on-google-vm"; then
    echo "gcloud-metadata: Google VM. No local service needed."
    exit 0
fi

unit="${HOME}/.config/systemd/user/gcloud-metadata.service"
existing=0
[[ -e "$unit" || -L "$unit" ]] && existing=1
mkdir -p "$(dirname "$unit")"
ensure_link "${DOTFILES_DIR}/devbox/gcloud-metadata.service" "$unit"
systemctl --user daemon-reload
if (( existing )) && ! systemctl --user is-active --quiet gcloud-metadata.service \
    && ! systemctl --user is-enabled --quiet gcloud-metadata.service; then
    echo "gcloud-metadata: keeping the disabled, inactive service stopped."
    exit 0
fi
systemctl --user enable --now gcloud-metadata.service
