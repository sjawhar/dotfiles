#!/bin/bash
set -eufx -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

completions_dir="${SCRIPT_DIR}/completions.d"
mkdir -p "${completions_dir}"

JJ_VERSION=0.27.0
if ! command -v jj &> /dev/null; then
    mkdir -p ~/.jj/bin
    curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
        | tar -xz -C ~/.jj
    chmod +x ~/.jj/jj
    mv ~/.jj/jj ~/.jj/bin/
    jj util completion bash > "${completions_dir}/jj.bash"
fi


