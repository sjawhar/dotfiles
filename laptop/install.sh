#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

LAPTOP_DIR="${DOTFILES_DIR}/laptop"

# =============================================================================
# Desktop packages (APT repos + install)
# =============================================================================
echo "--- Laptop packages ---"

# --- Add missing APT repositories ---
NEEDS_APT_UPDATE=false

# 1Password
if [ ! -f /etc/apt/sources.list.d/1password.list ]; then
    echo "Adding 1Password repo..."
    curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
        | sudo gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg 2>/dev/null
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" \
        | sudo tee /etc/apt/sources.list.d/1password.list >/dev/null
    NEEDS_APT_UPDATE=true
fi

# Brave Browser
if [ ! -f /etc/apt/sources.list.d/brave-browser-release.sources ]; then
    echo "Adding Brave repo..."
    curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg \
        | sudo tee /usr/share/keyrings/brave-browser-archive-keyring.gpg >/dev/null
    cat <<'REPO' | sudo tee /etc/apt/sources.list.d/brave-browser-release.sources >/dev/null
Types: deb
URIs: https://brave-browser-apt-release.s3.brave.com
Suites: stable
Components: main
Architectures: amd64 arm64
Signed-By: /usr/share/keyrings/brave-browser-archive-keyring.gpg
REPO
    NEEDS_APT_UPDATE=true
fi

# Docker
if [ ! -f /etc/apt/sources.list.d/docker.sources ]; then
    echo "Adding Docker repo..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-noble}")
    cat <<REPO | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
REPO
    NEEDS_APT_UPDATE=true
fi

# Tailscale
if [ ! -f /etc/apt/sources.list.d/tailscale.list ]; then
    echo "Adding Tailscale repo..."
    CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-noble}")
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.noarmor.gpg" \
        | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu ${CODENAME} main" \
        | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
    NEEDS_APT_UPDATE=true
fi

# VS Code
if [ ! -f /etc/apt/sources.list.d/vscode.sources ]; then
    echo "Adding VS Code repo..."
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg 2>/dev/null
    cat <<'REPO' | sudo tee /etc/apt/sources.list.d/vscode.sources >/dev/null
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/microsoft.gpg
REPO
    NEEDS_APT_UPDATE=true
fi

# PipeWire/WirePlumber upgrade (Ubuntu only, not Pop!_OS)
# Pop!_OS ships modern PipeWire (1.5+) and WirePlumber (0.5+) in its own repos.
# Stock Ubuntu 24.04 ships PipeWire 1.0.5 + WirePlumber 0.4.17, which has a
# completely broken bluez5 audio monitor — Bluetooth audio devices (e.g. DJI Mic
# Mini over HFP) never appear in PipeWire. The savoury1 PPA provides PipeWire
# 1.4.x + WirePlumber 0.5.x with working Bluetooth audio support.
if [[ "$(. /etc/os-release && echo "${ID}")" == "ubuntu" ]] && ! grep -qi 'pop' /etc/os-release 2>/dev/null; then
    if ! grep -rq 'savoury1/pipewire' /etc/apt/sources.list.d/ 2>/dev/null; then
        echo "Adding savoury1/pipewire PPA (Ubuntu needs newer PipeWire for BT audio)..."
        sudo add-apt-repository -y ppa:savoury1/pipewire
        NEEDS_APT_UPDATE=true
    fi
fi

# --- Single apt update if any repos were added ---
if $NEEDS_APT_UPDATE; then
    sudo apt-get update -qq
fi

# --- Install missing packages ---
LAPTOP_PKGS=(
    1password
    brave-browser
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    tailscale
    code
)
MISSING=()
for pkg in "${LAPTOP_PKGS[@]}"; do
    dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Installing: ${MISSING[*]}..."
    sudo apt-get install -y -qq "${MISSING[@]}" >/dev/null
fi

# --- Upgrade PipeWire/WirePlumber from savoury1 PPA (Ubuntu only) ---
# The savoury1 PPA renames some transient packages (libopenfec, libwebrtc-audio-
# processing1) which conflict with the stock Ubuntu versions. Remove the old ones
# first, then install the full set. This is idempotent — on Pop!_OS or if already
# upgraded, the PPA block above is skipped and these packages are already correct.
if grep -rq 'savoury1/pipewire' /etc/apt/sources.list.d/ 2>/dev/null; then
    PIPEWIRE_PKGS=(
        pipewire pipewire-bin pipewire-pulse pipewire-alsa pipewire-audio
        gstreamer1.0-pipewire
        libpipewire-0.3-0t64 libpipewire-0.3-common libpipewire-0.3-modules
        libspa-0.2-bluetooth libspa-0.2-modules
        wireplumber libwireplumber-0.5-0
    )
    # Remove conflicting transitional packages from stock Ubuntu if present
    for old_pkg in libpipewire-0.3-0 libwebrtc-audio-processing1 libopenfec; do
        if dpkg -s "$old_pkg" &>/dev/null 2>&1; then
            echo "Removing conflicting package: $old_pkg"
            sudo dpkg --remove --force-depends "$old_pkg"
        fi
    done
    MISSING=()
    for pkg in "${PIPEWIRE_PKGS[@]}"; do
        dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
    done
    if [ ${#MISSING[@]} -gt 0 ]; then
        echo "Upgrading PipeWire/WirePlumber from savoury1: ${MISSING[*]}..."
        sudo apt-get install -y -qq "${MISSING[@]}"
    fi
fi

# --- Post-install: Docker group ---
if command -v docker &>/dev/null && ! id -nG "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    echo "NOTE: Added $USER to docker group. Log out/in for it to take effect."
fi

# --- Post-install: Tailscale service ---
if command -v tailscale &>/dev/null; then
    sudo systemctl enable --now tailscaled 2>/dev/null || true
fi

# =============================================================================
# Flatpak apps
# =============================================================================
if ! command -v flatpak &>/dev/null; then
    sudo apt-get install -y -qq flatpak >/dev/null
fi
if command -v flatpak &>/dev/null; then
    if ! flatpak remote-list 2>/dev/null | grep -q flathub; then
        echo "Adding Flathub remote..."
        flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
    fi
    FLATPAK_APPS=(
        com.slack.Slack
        org.keepassxc.KeePassXC
        io.github.seadve.Kooha
    )
    for app in "${FLATPAK_APPS[@]}"; do
        if ! flatpak info "$app" &>/dev/null; then
            echo "Installing flatpak: $app..."
            flatpak install -y --noninteractive flathub "$app"
        fi
    done
fi

# =============================================================================
# Config & symlinks
# =============================================================================

# --- SSH config ---
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ensure_link "${LAPTOP_DIR}/ssh-config" ~/.ssh/config
chmod 600 ~/.ssh/config

# --- Aerospace-style workspace switching ---
chmod +x "${LAPTOP_DIR}/aerospace-ws"
ensure_link "${LAPTOP_DIR}/aerospace-ws" ~/.local/bin/aerospace-ws

# --- PipeWire: default webcam to 1080p30 instead of 480p25 ---
mkdir -p ~/.config/pipewire/pipewire.conf.d
ensure_link "${DOTFILES_DIR}/pipewire/10-video-quality.conf" ~/.config/pipewire/pipewire.conf.d/10-video-quality.conf

# --- COSMIC custom shortcuts ---
mkdir -p ~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1
ensure_link "${LAPTOP_DIR}/shortcuts-custom" \
    ~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom

# =============================================================================
# Desktop apps (separate installers)
# =============================================================================
source "${DOTFILES_DIR}/installers/ghostty.sh"
source "${DOTFILES_DIR}/installers/voxtype.sh"

echo "--- Laptop setup complete ---"
echo "Next steps:"
echo "  - Run 'sudo tailscale up' if not already authenticated"
echo "  - Run '${LAPTOP_DIR}/hardening.sh' for security hardening (UFW, USBGuard, etc.)"
