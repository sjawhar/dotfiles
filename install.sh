#!/bin/bash
set -eux -o pipefail

# ==============================================================================
# Configuration
# ==============================================================================

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTFILES_BIN_DIR="${DOTFILES_DIR}/bin"
DOTFILES_COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"

# Tool versions
JJ_VERSION=0.37.0
STARSHIP_VERSION=1.22.1
YQ_VERSION=4.45.1
NODE_VERSION=22.16.0

# ==============================================================================
# Helper Functions
# ==============================================================================

# Download a file to a temp location, then move atomically to destination
# Usage: download_to FILE_URL DEST_PATH
download_to() {
    local url="$1"
    local dest="$2"
    local tmp
    tmp=$(mktemp)
    trap "rm -f '$tmp'" RETURN
    curl -fsSL "$url" -o "$tmp"
    mv "$tmp" "$dest"
}

# Download and extract a tarball atomically
# Usage: download_and_extract URL DEST_DIR [TAR_FLAGS...]
download_and_extract() {
    local url="$1"
    local dest="$2"
    shift 2
    local tar_flags=("${@:--xz}")
    local tmp
    tmp=$(mktemp)
    trap "rm -f '$tmp'" RETURN
    curl -fsSL "$url" -o "$tmp"
    tar "${tar_flags[@]}" -C "$dest" -f "$tmp"
}

# Check version and install/upgrade if needed
# Usage: ensure_version TOOL TARGET_VERSION VERSION_CMD INSTALL_FN
ensure_version() {
    local tool="$1"
    local target_version="$2"
    local version_cmd="$3"
    local install_fn="$4"

    if ! command -v "$tool" &> /dev/null; then
        "$install_fn"
    else
        local current
        current=$(eval "$version_cmd") || current=""
        if [ "$current" != "$target_version" ]; then
            echo "Upgrading $tool from v$current to v$target_version"
            "$install_fn"
        fi
    fi
}

# ==============================================================================
# Platform Detection
# ==============================================================================

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux)   OS_TYPE="linux"  ;;
    Darwin)  OS_TYPE="darwin" ;;
    *)       echo "Unsupported OS: $OS"; exit 1 ;;
esac

case "$ARCH" in
    x86_64)         ARCH_NODE="x64"    ARCH_YQ="amd64"  ARCH_JJ="x86_64"  ;;
    aarch64|arm64)  ARCH_NODE="arm64"  ARCH_YQ="arm64"  ARCH_JJ="aarch64" ;;
    *)              echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

if [ "$OS_TYPE" = "darwin" ]; then
    SHELL_RC="${HOME}/.zshrc"
    SHELL_TYPE="zsh"
else
    SHELL_RC="${HOME}/.bashrc"
    SHELL_TYPE="bash"
fi

# ==============================================================================
# Shell & Git Configuration
# ==============================================================================

if [ ! -f "$SHELL_RC" ] || ! grep -q ".dotfiles" "$SHELL_RC"; then
    echo "Adding .dotfiles to $SHELL_RC"
    echo "" >> "$SHELL_RC"
    echo '[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"' >> "$SHELL_RC"
fi

source "${HOME}/.dotfiles/.bashrc"

if [ ! -f "${HOME}/.gitconfig" ] || ! grep -q ".dotfiles" "${HOME}/.gitconfig"; then
    echo "" >> "${HOME}/.gitconfig"
    cat <<EOF >> "${HOME}/.gitconfig"
[include]
    path = ${HOME}/.dotfiles/.gitconfig
EOF
fi

mkdir -p "${DOTFILES_BIN_DIR}" "${DOTFILES_COMPLETIONS_DIR}"

# ==============================================================================
# jj (Jujutsu)
# ==============================================================================

install_jj() {
    local platform
    if [ "$OS_TYPE" = "darwin" ]; then
        platform="${ARCH_JJ}-apple-darwin"
    else
        platform="${ARCH_JJ}-unknown-linux-musl"
    fi

    download_and_extract \
        "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-${platform}.tar.gz" \
        "${DOTFILES_BIN_DIR}" \
        -xz

    chmod +x "${DOTFILES_BIN_DIR}/jj"
    "${DOTFILES_BIN_DIR}/jj" util completion "$SHELL_TYPE" > "${DOTFILES_COMPLETIONS_DIR}/jj.${SHELL_TYPE}"
}

ensure_version jj "$JJ_VERSION" \
    "jj --version | awk '{split(\$2,a,\"-\"); print a[1]}'" \
    install_jj

# jj user config (environment-specific, not in dotfiles)
# Skip in devpod build (non-interactive)
JJ_USER_CONFIG="${HOME}/.config/jj/config.toml"
mkdir -p "$(dirname "${JJ_USER_CONFIG}")"

if [ ! -f "${JJ_USER_CONFIG}" ]; then
    if [ "${DEVPOD_BUILD:-}" = "1" ] || [ ! -t 0 ]; then
        echo "Skipping jj user config (non-interactive environment, configure manually)"
    else
        echo "Setting up jj user config..."
        read -rp "Enter your email for jj commits: " JJ_USER_EMAIL
        cat > "${JJ_USER_CONFIG}" <<EOF
# Environment-specific jj config (NOT checked into dotfiles)

[user]
name = "Sami Jawhar"
email = "${JJ_USER_EMAIL}"
EOF
        echo "Created ${JJ_USER_CONFIG}"
    fi
fi

# ==============================================================================
# Starship
# ==============================================================================

install_starship() {
    curl -sS https://starship.rs/install.sh \
        | sh -s -- \
            --bin-dir "${DOTFILES_BIN_DIR}" \
            --version "v${STARSHIP_VERSION}" \
            --yes
}

ensure_version starship "$STARSHIP_VERSION" \
    "starship --version | awk 'NR==1 {print \$2}'" \
    install_starship

# ==============================================================================
# yq
# ==============================================================================

install_yq() {
    download_to \
        "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_${OS_TYPE}_${ARCH_YQ}" \
        "${DOTFILES_BIN_DIR}/yq"
    chmod +x "${DOTFILES_BIN_DIR}/yq"
}

ensure_version yq "$YQ_VERSION" \
    "yq --version | awk '{print \$4}' | sed 's/^v//'" \
    install_yq

# ==============================================================================
# Node.js
# ==============================================================================

install_node() {
    local node_dir="${DOTFILES_DIR}/.node"
    local tmp_dir
    tmp_dir=$(mktemp -d)
    trap "rm -rf '$tmp_dir'" RETURN

    download_and_extract \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-${OS_TYPE}-${ARCH_NODE}.tar.xz" \
        "$tmp_dir" \
        -xJ --strip-components=1

    # Atomic swap: remove old, move new into place
    rm -rf "$node_dir"
    mv "$tmp_dir" "$node_dir"

    # Symlink executables (node is a file, npm/npx are symlinks)
    for file in "${node_dir}"/bin/*; do
        [ -e "$file" ] || continue
        ln -sf "$file" "${DOTFILES_BIN_DIR}/$(basename "$file")"
    done
}

ensure_version node "$NODE_VERSION" \
    "node --version | sed 's/^v//'" \
    install_node

# ==============================================================================
# Claude Code
# ==============================================================================

mkdir -p "${NPM_CONFIG_PREFIX}"

if ! command -v claude &> /dev/null; then
    npm install -g @anthropic-ai/claude-code
fi

# ==============================================================================
# Initialize dotfiles repo
# ==============================================================================

# Skip in devpod build (it's a copy, not a clone)
if [ "${DEVPOD_BUILD:-}" != "1" ]; then
    cd "${DOTFILES_DIR}"
    if ! "${DOTFILES_BIN_DIR}/jj" st &> /dev/null; then
        "${DOTFILES_BIN_DIR}/jj" git init --colocate
    fi
fi
