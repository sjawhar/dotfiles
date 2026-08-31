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
    local url="$1" name="$2" ref="${3:-}"
    local dir="${DOTFILES_DIR}/vendor/${name}"
    if [ ! -e "${dir}/.git" ]; then
        mkdir -p "$(dirname "$dir")"
        git clone --depth 1 "$url" "$dir"
    fi
    # Pinned vendors converge every machine on one commit; unpinned ones stay
    # at whatever HEAD they were cloned at (never auto-updated).
    if [ -n "$ref" ] && [ "$(git -C "$dir" rev-parse HEAD)" != "$ref" ]; then
        git -C "$dir" fetch --depth 1 origin "$ref"
        git -C "$dir" checkout --detach "$ref"
    fi
    if [ ! -e "${dir}/.jj" ] && command -v jj &>/dev/null; then
        ( cd "$dir" && jj git init --colocate )
    fi
}

ensure_command() {
    local name="$1" install_cmd="$2"
    local found
    found=$(command -v "$name" 2>/dev/null) || true
    # Skip shims — they wrap the real binary but don't mean it's installed
    if [ -n "$found" ] && [[ "$found" != "${DOTFILES_DIR}/shims/"* ]]; then
        return 0
    fi
    echo "Installing ${name}..."
    eval "$install_cmd"
    hash -r
    command -v "$name" &>/dev/null || { echo "${name} not found on PATH after install" >&2; return 1; }
}

ensure_json() {
    local file="$1" check="$2" transform="$3" desc="${4:-}"
    jq -e "$check" "$file" > /dev/null 2>&1 && return 0
    [ -n "$desc" ] && echo "$desc"
    # Write to the symlink target, not the symlink path: mv onto a symlink
    # replaces the link with a plain file, silently forking live config from
    # its dotfiles canonical (this destroyed the opencode.json link once).
    local real tmp
    real=$(readlink -f "$file")
    tmp=$(mktemp)
    jq "$transform" "$file" > "$tmp" && mv "$tmp" "$real"
}
