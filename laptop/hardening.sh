#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

echo "--- Hardening ---"


# --- UFW firewall ---
if command -v ufw &>/dev/null; then
    if ! sudo ufw status | grep -q "Status: active"; then
        echo "Enabling UFW firewall..."
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw --force enable
    fi
else
    echo "Installing and enabling UFW firewall..."
    sudo apt-get install -y -qq ufw >/dev/null
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw --force enable
fi

# --- Unattended upgrades (automatic security patches) ---
if ! dpkg -s unattended-upgrades &>/dev/null; then
    echo "Installing unattended-upgrades..."
    sudo apt-get install -y -qq unattended-upgrades >/dev/null
fi
AUTO_UPGRADES_CONF="/etc/apt/apt.conf.d/20auto-upgrades"
if [ ! -f "$AUTO_UPGRADES_CONF" ]; then
    echo "Enabling automatic security updates..."
    printf '%s\n' \
        'APT::Periodic::Update-Package-Lists "1";' \
        'APT::Periodic::Unattended-Upgrade "1";' \
        | sudo tee "$AUTO_UPGRADES_CONF" >/dev/null
fi

# --- DNS-over-TLS (opportunistic) via systemd-resolved ---
RESOLVED_CONF="/etc/systemd/resolved.conf"
if [ -f "$RESOLVED_CONF" ] && ! grep -q '^DNSOverTLS=opportunistic' "$RESOLVED_CONF" 2>/dev/null; then
    echo "Enabling DNS-over-TLS (opportunistic)..."
    if grep -q '^#\?DNSOverTLS=' "$RESOLVED_CONF"; then
        sudo sed -i 's/^#\?DNSOverTLS=.*/DNSOverTLS=opportunistic/' "$RESOLVED_CONF"
    else
        # Append under [Resolve] section
        sudo sed -i '/^\[Resolve\]/a DNSOverTLS=opportunistic' "$RESOLVED_CONF"
    fi
    sudo systemctl restart systemd-resolved
fi

# --- USBGuard (block rogue USB devices) ---
# See usbguard-rules.conf for policy design and new-dock workflow.
# We do NOT use generate-policy — it over-constrains with parent-hash and
# via-port, which breaks when you change docks. The rules.conf is manually
# curated.
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
if ! dpkg -s usbguard &>/dev/null; then
    echo "Installing USBGuard..."
    sudo apt-get install -y -qq usbguard >/dev/null
    sudo systemctl enable --now usbguard
fi
if [ -f "$SCRIPT_DIR/usbguard-rules.conf" ]; then
    echo "Installing USBGuard rules..."
    sudo cp "$SCRIPT_DIR/usbguard-rules.conf" /etc/usbguard/rules.conf
    sudo chmod 600 /etc/usbguard/rules.conf
    sudo systemctl restart usbguard
fi

# --- Firmware updates ---
if command -v fwupdmgr &>/dev/null; then
    echo ""
    echo "Checking for firmware updates..."
    sudo fwupdmgr refresh --force 2>/dev/null || true
    if sudo fwupdmgr get-updates 2>/dev/null | grep -q 'New version'; then
        echo "WARNING: Firmware updates available! Run: sudo fwupdmgr update"
        echo "  (Requires AC power and a reboot)"
    else
        echo "Firmware is up to date."
    fi
fi
