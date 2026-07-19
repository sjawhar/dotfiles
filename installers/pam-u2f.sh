#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Hardware security key (FIDO2/U2F) touch-to-auth for sudo/login via pam_u2f.
# Host-only (Pop!_OS laptop) — not sourced from install.sh. Run directly.
#
# Ubuntu 24.04 ships libpam-u2f 1.1.0 which is vulnerable to CVE-2025-23013
# (partial auth bypass, fixed in 1.3.1). Yubico's PPA provides current 1.4.x.

# --- Yubico PPA ---
if ! grep -rq "yubico/stable" /etc/apt/sources.list.d/ 2>/dev/null; then
    echo "Adding Yubico stable PPA..."
    sudo add-apt-repository -y ppa:yubico/stable >/dev/null
fi

# --- pam_u2f module + enrollment helper ---
if ! dpkg -s libpam-u2f pamu2fcfg &>/dev/null; then
    sudo apt-get install -y -qq libpam-u2f pamu2fcfg >/dev/null
fi

# --- Central authfile location ---
# /etc/Yubico (not ~/.config/Yubico) so PAM can read it before an encrypted
# home is unlocked. File is created by enrollment, not by this installer:
#   pamu2fcfg | sudo tee /etc/Yubico/u2f_keys        # first key
#   pamu2fcfg -n | sudo tee -a /etc/Yubico/u2f_keys  # each backup key
sudo mkdir -p /etc/Yubico

# --- Shared PAM fragment (inert until included from a PAM stack) ---
PAM_U2F_LINE='auth required pam_u2f.so authfile=/etc/Yubico/u2f_keys cue'
if ! grep -qxF "$PAM_U2F_LINE" /etc/pam.d/u2f 2>/dev/null; then
    echo "$PAM_U2F_LINE" | sudo tee /etc/pam.d/u2f >/dev/null
fi

# --- Wire into sudo, gated on enrollment ---
# Including pam_u2f as "required" with zero enrolled keys would brick sudo,
# so this step is skipped loudly until /etc/Yubico/u2f_keys has an entry.
SUDO_INCLUDE='@include u2f'
if [ ! -s /etc/Yubico/u2f_keys ]; then
    echo "NOTE: /etc/Yubico/u2f_keys is empty — skipping sudo PAM wiring."
    echo "  Enroll a key (see comments above), then re-run this installer."
elif ! grep -qxF "$SUDO_INCLUDE" /etc/pam.d/sudo; then
    echo "Wiring pam_u2f into /etc/pam.d/sudo..."
    echo "  Keep a root shell open and verify 'sudo true' in a NEW terminal."
    sudo sed -i "/^@include common-auth/a ${SUDO_INCLUDE}" /etc/pam.d/sudo
fi
