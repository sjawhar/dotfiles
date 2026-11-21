#!/bin/bash
# Names the box's uv, pip and bun caches in each tool's own user config file, on a machine with the
# big disk (devbox/install.sh runs this): ~/.config/uv/uv.toml, ~/.config/pip/pip.conf and
# ~/.bunfig.toml (the first two under $XDG_CONFIG_HOME, and the third in it, when that is set).
#
# .bashrc and scripts/agent-session export UV_CACHE_DIR, PIP_CACHE_DIR and BUN_INSTALL_CACHE_DIR,
# and a tool that does not inherit them falls back to its home default (~/.cache/uv,
# ~/.cache/pip, ~/.bun/install/cache), which nothing caps. omp's Python eval kernel passes its
# tools only an allow-list of variables without them (packages/coding-agent/src/eval/py/runtime.ts
# in oh-my-pi), and a systemd user unit gets neither file's variables. Each tool reads its config
# file for a setting its environment does not give, so those runs now use the caches
# devbox/nvme-cache-cap holds. A variable a process has still wins, so the shells, the agent
# sessions and an agent box (whose home has none of these files) keep theirs. installers/go-env.sh
# does the same for Go. CARGO_HOME and HF_HOME have no config file to name them in.
#
# The values are the ones .bashrc and scripts/agent-session set: uv's and pip's caches in
# <scratch>/cache/uv and <scratch>/cache/pip, where <scratch> is /mnt/scratch/<user> when
# devbox/gce-disks made it and /mnt/nvme/<user> otherwise, with uv's link-mode = "copy" on the
# scratch disk (a venv on another disk cannot hardlink from it), and bun's in
# /mnt/nvme/<user>/cache/bun.
#
# Each file this writes starts with a marker line, and only a file that starts with it is rewritten.
# A file someone else wrote is left as it is, with a warning naming the lines to add by hand. A
# machine without /mnt/nvme/<user> is left alone.
#
# CACHE_CONFIG_NVME_HOME and CACHE_CONFIG_SCRATCH_HOME replace /mnt/nvme/<user> and
# /mnt/scratch/<user>, so a test can run this against a scratch tree.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

marker="# Written by installers/cache-config.sh in the dotfiles. Delete this line to keep your own edits."

user="$(id -un)"
nvme_home="${CACHE_CONFIG_NVME_HOME:-/mnt/nvme/$user}"
scratch_home="${CACHE_CONFIG_SCRATCH_HOME:-/mnt/scratch/$user}"
if [ ! -d "$nvme_home" ]; then
    echo "cache-config: no $nvme_home, so the uv, pip and bun config files are left alone"
    exit 0
fi
cache_home="$nvme_home" link_mode=""
if [ -d "$scratch_home" ]; then
    cache_home="$scratch_home"
    link_mode=$'\nlink-mode = "copy"'
fi
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"

# write_config FILE CONTENT: put CONTENT, after the marker line, in FILE, unless FILE is someone
# else's (it exists and does not start with the marker). A second run with the same CONTENT changes
# nothing.
write_config() {
    local file="$1" want="$marker"$'\n'"$2" have="" first="" tmp
    if [ -e "$file" ] || [ -L "$file" ]; then
        { IFS= read -r first <"$file"; } 2>/dev/null || true
        if [ "$first" != "$marker" ]; then
            echo "WARNING: cache-config: $file was not written here, so it is left as it is; add by hand:" >&2
            printf '%s\n' "$2" | sed 's/^/    /' >&2
            return 0
        fi
        have="$(cat "$file")"
        if [ "$have" = "$want" ]; then
            echo "cache-config: $file is already up to date"
            return 0
        fi
    fi
    mkdir -p "$(dirname "$file")"
    tmp="$(mktemp "$file.XXXXXX")"
    printf '%s\n' "$want" >"$tmp"
    mv "$tmp" "$file"
    echo "cache-config: wrote $file"
}

write_config "$config_home/uv/uv.toml" "cache-dir = \"$cache_home/cache/uv\"$link_mode"
write_config "$config_home/pip/pip.conf" "[global]
cache-dir = $cache_home/cache/pip"
write_config "${XDG_CONFIG_HOME:-$HOME}/.bunfig.toml" "[install.cache]
dir = \"$nvme_home/cache/bun\""
