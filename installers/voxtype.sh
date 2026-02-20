#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VOXTYPE_VERSION="0.6.2"
VOXTYPE_ARCH="amd64"  # only x86_64 supported for now

# --- .deb package ---
installed_version=$(dpkg-query -W -f='${Version}' voxtype 2>/dev/null || echo "")
if [ "$installed_version" != "${VOXTYPE_VERSION}-1" ]; then
    echo "Installing voxtype ${VOXTYPE_VERSION}..."
    deb_url="https://github.com/peteonrails/voxtype/releases/download/v${VOXTYPE_VERSION}/voxtype_${VOXTYPE_VERSION}-1_${VOXTYPE_ARCH}.deb"
    tmp_deb="$(mktemp --suffix=.deb)"
    trap 'rm -f "$tmp_deb"' EXIT
    curl -fSL -o "$tmp_deb" "$deb_url"
    sudo dpkg -i "$tmp_deb" || sudo apt-get install -f -y
    rm -f "$tmp_deb"
    trap - EXIT
fi

# --- apt dependencies ---
if ! dpkg -s ydotool gir1.2-ayatanaappindicator3-0.1 &>/dev/null; then
    sudo apt-get install -y -qq ydotool gir1.2-ayatanaappindicator3-0.1 >/dev/null
fi

# --- udev rule for /dev/uinput access (ydotool) ---
UDEV_RULE='KERNEL=="uinput", GROUP="input", MODE="0660"'
UDEV_FILE="/etc/udev/rules.d/99-uinput.rules"
if ! grep -qxF "$UDEV_RULE" "$UDEV_FILE" 2>/dev/null; then
    echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger /dev/uinput 2>/dev/null || true
fi

# --- input group ---
if ! id -nG "$USER" | grep -qw input; then
    sudo usermod -aG input "$USER"
    echo "NOTE: Added $USER to input group. Reboot required for systemd to pick it up."
fi

# --- Symlinks ---
mkdir -p ~/.config/voxtype
mkdir -p ~/.config/autostart
mkdir -p ~/.config/systemd/user/voxtype.service.d

ensure_link "${DOTFILES_DIR}/voxtype/config.toml"            ~/.config/voxtype/config.toml
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-tray.desktop"    ~/.config/autostart/voxtype-tray.desktop
ensure_link "${DOTFILES_DIR}/voxtype/groq.conf"               ~/.config/systemd/user/voxtype.service.d/groq.conf

# --- Services ---
sudo systemctl enable --now ydotool 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now voxtype 2>/dev/null || true

# --- Manual step reminders ---
if [ ! -f ~/.config/sops/age/keys.txt ]; then
    echo "WARNING: ~/.config/sops/age/keys.txt not found."
    echo "  The voxtype daemon needs this to decrypt the Groq API key."
    echo "  Write your age private key to this file."
fi

echo "NOTE: Configure COSMIC keyboard shortcut manually:"
echo "  Settings > Keyboard > Custom Shortcuts"
echo "  Name: Voxtype Toggle"
echo "  Command: voxtype record toggle"
echo "  Shortcut: Super+V (or your preference)"
