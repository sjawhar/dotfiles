#!/bin/bash
set -eufx -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! grep ".dotfiles" "${HOME}/.bashrc"; then
    echo '[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"' >> "${HOME}/.bashrc"
fi

completions_dir="${SCRIPT_DIR}/completions.d"
mkdir -p "${completions_dir}"

JJ_VERSION=0.27.0
JJ_HOME="${HOME}/.jj"
if ! command -v jj &> /dev/null; then
    mkdir -p "${JJ_HOME}/bin"
    curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
        | tar -xz -C "${HOME}/.jj"
    chmod +x "${JJ_HOME}/jj"
    mv "${JJ_HOME}/jj" "${JJ_HOME}/bin/"
    "${JJ_HOME}/bin/jj" util completion bash > "${completions_dir}/jj.bash"
fi

. "${HOME}/.bashrc"