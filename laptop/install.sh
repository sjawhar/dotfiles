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
if command -v flatpak &>/dev/null; then
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
