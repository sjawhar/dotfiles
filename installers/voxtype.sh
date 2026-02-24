#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VOXTYPE_VERSION="0.6.2"
YDOTOOL_VERSION="1.0.4"
# --- Patched voxtype binary ---
# Builds from source with shift_enter_newlines support for ydotool driver.
# TODO: Remove patch step once upstream merges the fix.
VOXTYPE_PATCH="${DOTFILES_DIR}/voxtype/ydotool-shift-enter.patch"
VOXTYPE_BIN="${DOTFILES_DIR}/bin/voxtype"
if [ ! -x "$VOXTYPE_BIN" ]; then
    echo "Building voxtype ${VOXTYPE_VERSION} from source..."
    VOXTYPE_SRC="${HOME}/Code/voxtype"
    if [ ! -d "${VOXTYPE_SRC}/.git" ]; then
        git clone --depth 1 --branch "v${VOXTYPE_VERSION}" \
            https://github.com/peteonrails/voxtype.git "$VOXTYPE_SRC"
    fi
    git -C "$VOXTYPE_SRC" checkout -f "v${VOXTYPE_VERSION}" 2>/dev/null || true
    if [ -f "$VOXTYPE_PATCH" ]; then
        git -C "$VOXTYPE_SRC" apply "$VOXTYPE_PATCH" 2>/dev/null || true
    fi
    if ! dpkg -s libasound2-dev libvulkan-dev libclang-dev cmake &>/dev/null; then
        sudo apt-get install -y -qq libasound2-dev libvulkan-dev pkg-config libclang-dev cmake >/dev/null
    fi
    (cd "$VOXTYPE_SRC" && cargo build --release --features gpu-vulkan)
    cp "${VOXTYPE_SRC}/target/release/voxtype" "$VOXTYPE_BIN"
    chmod +x "$VOXTYPE_BIN"
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
ensure_link "${DOTFILES_DIR}/voxtype/config.toml"               ~/.config/voxtype/config.toml
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-tray.desktop"     ~/.config/autostart/voxtype-tray.desktop
ensure_link "${DOTFILES_DIR}/voxtype/voxtype.service"          ~/.config/systemd/user/voxtype.service
ensure_link "${DOTFILES_DIR}/voxtype/ydotoold.service"          ~/.config/systemd/user/ydotoold.service
MODEL_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/voxtype/models/ggml-large-v3-turbo.bin"
if [ ! -f "$MODEL_FILE" ]; then
    echo "Downloading whisper large-v3-turbo model (~1.6 GB)..."
    voxtype setup --download --model large-v3-turbo --quiet
fi
# --- Services ---
systemctl --user daemon-reload
systemctl --user enable --now ydotoold 2>/dev/null || true
systemctl --user enable --now voxtype 2>/dev/null || true
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
