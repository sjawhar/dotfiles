#!/bin/bash
# Shared helpers for dotfiles installers

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

export PATH="${DOTFILES_DIR}/bin:${HOME}/.local/bin:$PATH"

ensure_link() { ln -sfn "$1" "$2"; }

ensure_clone() {
    local url="$1" dir="$2"
    [ -e "${dir}/.git" ] && return 0
    if [ -e "$dir" ]; then
        echo "Removing non-git directory: $dir" >&2
        rm -rf "$dir"
    fi
    mkdir -p "$(dirname "$dir")"
    git clone --depth 1 "$url" "$dir"
}

ensure_vendor() {
    local url="$1" name="$2"
    local dir="${DOTFILES_DIR}/vendor/${name}"
    if [ -e "${dir}/.git" ]; then
        if [ ! -e "${dir}/.jj" ] && command -v jj &>/dev/null; then
            ( cd "$dir" && jj git init --colocate )
        fi
        return 0
    fi
    mkdir -p "$(dirname "$dir")"
    git clone --depth 1 "$url" "$dir"
    if command -v jj &>/dev/null; then
        ( cd "$dir" && jj git init --colocate )
    fi
}

ensure_command() {
    local name="$1" install_cmd="$2"
    command -v "$name" &>/dev/null && return 0
    echo "Installing ${name}..."
    eval "$install_cmd"
    hash -r
    command -v "$name" &>/dev/null || { echo "${name} not found on PATH after install" >&2; return 1; }
}

ensure_json() {
    local file="$1" check="$2" transform="$3" desc="${4:-}"
    jq -e "$check" "$file" > /dev/null 2>&1 && return 0
    [ -n "$desc" ] && echo "$desc"
    local tmp=$(mktemp)
    jq "$transform" "$file" > "$tmp" && mv "$tmp" "$file"
}
