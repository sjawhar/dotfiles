#!/bin/bash
set -eufx -o pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTFILES_BIN_DIR="${DOTFILES_DIR}/bin"
DOTFILES_COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"

if ! grep ".dotfiles" "${HOME}/.bashrc"; then
    echo "Adding .dotfiles to .bashrc"
    echo "" >> "${HOME}/.bashrc"
    echo '[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"' >> "${HOME}/.bashrc"
fi

source "${HOME}/.dotfiles/.bashrc"

if ! grep ".dotfiles" "${HOME}/.gitconfig"; then
    cat <<EOF >> "${HOME}/.gitconfig"
[include]
    path = ${HOME}/.dotfiles/.gitconfig
EOF
fi

mkdir -p "${DOTFILES_BIN_DIR}"
mkdir -p "${DOTFILES_COMPLETIONS_DIR}"

JJ_VERSION=0.33.0
install_jj() {
    curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
        | tar -xz -C "${DOTFILES_BIN_DIR}"
    chmod +x "${DOTFILES_BIN_DIR}/jj"
    "${DOTFILES_BIN_DIR}/jj" util completion bash > "${DOTFILES_COMPLETIONS_DIR}/jj.bash"
}

if ! command -v jj &> /dev/null; then
    install_jj
else
    CURRENT_JJ_VERSION=$(jj --version | awk '{print $2}' | awk -F '-' '{print $1}')
    if [ "$CURRENT_JJ_VERSION" != "$JJ_VERSION" ]; then
        echo "Upgrading jj from v$CURRENT_JJ_VERSION to v$JJ_VERSION"
        install_jj
    fi
fi

STARSHIP_VERSION=1.22.1
if ! command -v starship &> /dev/null; then
    curl -sS https://starship.rs/install.sh \
        | sh -s -- \
        --bin-dir "${DOTFILES_BIN_DIR}" \
        --version "v${STARSHIP_VERSION}" \
        --yes
fi

YQ_VERSION=4.45.1
if ! command -v yq &> /dev/null; then
    [ $(uname -m) == "aarch64" ] && ARCH="arm64" || ARCH="amd64"
    curl -fsSL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${ARCH}" -o "${DOTFILES_BIN_DIR}/yq"
    chmod +x "${DOTFILES_BIN_DIR}/yq"
fi

NODE_VERSION=22.16.0
if ! command -v node &> /dev/null; then
    [ $(uname -m) == "aarch64" ] && ARCH="arm64" || ARCH="x64"
    mkdir -p "${DOTFILES_DIR}/.node"
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" \
        | tar -xJ -C "${DOTFILES_DIR}/.node" --strip-components=1

    while read -r file; do
        ln -s "$file" "${DOTFILES_BIN_DIR}/$(basename "$file")"
    done < <(find "${DOTFILES_DIR}/.node/bin" -executable -print)
fi

mkdir -p "${NPM_CONFIG_PREFIX}"
if ! command -v claude &> /dev/null; then
    npm install -g @anthropic-ai/claude-code
fi

cd "${DOTFILES_DIR}"
if ! ${DOTFILES_BIN_DIR}/jj st &> /dev/null; then
    ${DOTFILES_BIN_DIR}/jj git init --colocate
fi
