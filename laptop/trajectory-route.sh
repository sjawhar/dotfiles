#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

# =============================================================================
# trajectory-route - Trajectory's prod VPC and internal.trajectorylabs.com DNS
# through the devbox (`ssh devbox-ssm`), for a machine on Theorem's tailnet.
# =============================================================================
echo "--- trajectory-route (Trajectory VPC through the devbox) ---"

dpkg -s sshuttle &>/dev/null || sudo apt-get install -y -qq sshuttle >/dev/null

UNIT="trajectory-route@.service"
if ! diff -q "${DOTFILES_DIR}/laptop/${UNIT}" "/etc/systemd/system/${UNIT}" &>/dev/null; then
    echo "Installing ${UNIT}..."
    sudo cp "${DOTFILES_DIR}/laptop/${UNIT}" "/etc/systemd/system/${UNIT}"
    sudo systemctl daemon-reload
    sudo systemctl try-restart "trajectory-route@${USER}.service"
fi
sudo systemctl enable --now "trajectory-route@${USER}.service"
