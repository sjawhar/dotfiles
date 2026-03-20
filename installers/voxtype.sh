#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

YDOTOOL_VERSION="1.0.4"
# --- voxtype binary from sjawhar fork release ---
# Custom fork with eager processing wiring. Downloads latest sami-tagged release.
# Once upstream merges PR #275 and cuts a release, switch back to peteonrails/voxtype.
VOXTYPE_REPO="sjawhar/voxtype"
VOXTYPE_BIN="${DOTFILES_DIR}/bin/voxtype"
VOXTYPE_INSTALLED_TAG="${DOTFILES_DIR}/bin/.voxtype-tag"

# Get latest sami release tag
VOXTYPE_TAG=$(gh release list --repo "$VOXTYPE_REPO" --limit 1 --json tagName -q '.[0].tagName' 2>/dev/null || true)
if [ -z "$VOXTYPE_TAG" ]; then
    echo "WARNING: Could not fetch latest voxtype release tag from $VOXTYPE_REPO"
    echo "  Keeping existing binary (if any)."
elif [ ! -x "$VOXTYPE_BIN" ] || [ ! -f "$VOXTYPE_INSTALLED_TAG" ] || [ "$(cat "$VOXTYPE_INSTALLED_TAG")" != "$VOXTYPE_TAG" ]; then
    echo "Installing voxtype ${VOXTYPE_TAG} from ${VOXTYPE_REPO}..."
    VOXTYPE_URL="https://github.com/${VOXTYPE_REPO}/releases/download/${VOXTYPE_TAG}/voxtype-${VOXTYPE_TAG}-linux-x86_64.tar.gz"
    curl -fSL "$VOXTYPE_URL" | tar xz -C "${DOTFILES_DIR}/bin/" voxtype
    chmod +x "$VOXTYPE_BIN"
    echo "$VOXTYPE_TAG" > "$VOXTYPE_INSTALLED_TAG"
fi

# --- ydotool v1.x (from GitHub release) ---
# The apt package is v0.1.8 which lacks proper key command support.
YDOTOOL_BIN="${DOTFILES_DIR}/bin/ydotool"
YDOTOOLD_BIN="${DOTFILES_DIR}/bin/ydotoold"
if [ ! -x "$YDOTOOL_BIN" ] || [ ! -x "$YDOTOOLD_BIN" ]; then
    echo "Installing ydotool ${YDOTOOL_VERSION}..."
    ydotool_base="https://github.com/ReimuNotMoe/ydotool/releases/download/v${YDOTOOL_VERSION}"
    curl -fSL -o "$YDOTOOL_BIN" "${ydotool_base}/ydotool-release-ubuntu-latest"
    curl -fSL -o "$YDOTOOLD_BIN" "${ydotool_base}/ydotoold-release-ubuntu-latest"
    chmod +x "$YDOTOOL_BIN" "$YDOTOOLD_BIN"
fi
# --- apt dependencies ---
if ! dpkg -s gir1.2-ayatanaappindicator3-0.1 wl-clipboard &>/dev/null; then
    sudo apt-get install -y -qq gir1.2-ayatanaappindicator3-0.1 wl-clipboard >/dev/null
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
mkdir -p ~/.config/systemd/user
ensure_link "${DOTFILES_DIR}/voxtype/config.toml"               ~/.config/voxtype/config.toml
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-tray.desktop"     ~/.config/autostart/voxtype-tray.desktop
ensure_link "${DOTFILES_DIR}/voxtype/voxtype.service"          ~/.config/systemd/user/voxtype.service
ensure_link "${DOTFILES_DIR}/voxtype/ydotoold.service"          ~/.config/systemd/user/ydotoold.service
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-recordings-sync.service" ~/.config/systemd/user/voxtype-recordings-sync.service
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-recordings-sync.timer"   ~/.config/systemd/user/voxtype-recordings-sync.timer
MODEL_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/voxtype/models/ggml-large-v3-turbo.bin"
if [ ! -f "$MODEL_FILE" ]; then
    echo "Downloading whisper large-v3-turbo model (~1.6 GB)..."
    voxtype setup --download --model large-v3-turbo --quiet
fi
# --- Services ---
systemctl --user daemon-reload
systemctl --user enable --now ydotoold 2>/dev/null || true
systemctl --user enable --now voxtype 2>/dev/null || true
systemctl --user enable --now voxtype-recordings-sync.timer 2>/dev/null || true
if [ ! -f ~/.config/sops/age/keys.txt ]; then
    echo "WARNING: ~/.config/sops/age/keys.txt not found."
    echo "  The voxtype daemon needs this to decrypt the Groq API key."
    echo "  Write your age private key to this file."
fi
echo "NOTE: COSMIC keyboard shortcuts are installed via laptop/install.sh"
