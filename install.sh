#!/bin/bash
set -eufx -o pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTFILES_BIN_DIR="${DOTFILES_DIR}/bin"
DOTFILES_COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"

if ! grep ".dotfiles" "${HOME}/.bashrc"; then
    echo '[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"' >> "${HOME}/.bashrc"
fi

if ! grep ".dotfiles" "${HOME}/.gitconfig"; then
    cat <<EOF >> "${HOME}/.gitconfig"
[include]
    path = ${HOME}/.dotfiles/.gitconfig
EOF
fi

mkdir -p "${DOTFILES_BIN_DIR}"
mkdir -p "${DOTFILES_COMPLETIONS_DIR}"

JJ_VERSION=0.27.0
if ! command -v jj &> /dev/null; then
    curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
        | tar -xz -C "${DOTFILES_BIN_DIR}"
    chmod +x "${DOTFILES_BIN_DIR}/jj"
    "${DOTFILES_BIN_DIR}/jj" util completion bash > "${DOTFILES_COMPLETIONS_DIR}/jj.bash"
fi

STARSHIP_VERSION=1.22.1
if ! command -v starship &> /dev/null; then
    curl -sS https://starship.rs/install.sh \
        | sh -s -- \
        --bin-dir "${DOTFILES_BIN_DIR}" \
        --version "v${STARSHIP_VERSION}" \
        --yes
fi

. "${HOME}/.bashrc"