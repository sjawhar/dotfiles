#!/bin/bash
# Names the box's Go build and module caches in Go's own env file, the file `go env -w` writes
# ($GOENV, else ~/.config/go/env), on a machine with the big disk (devbox/install.sh runs this).
#
# .bashrc and scripts/agent-session export GOCACHE and GOMODCACHE, and a go command that does not
# inherit them falls back to Go's defaults, ~/.cache/go-build and ~/go/pkg/mod, which nothing put
# under a cap. Two kinds of process do not inherit them: a systemd user unit, since the manager
# reads environment.d, which names neither; and every tool omp's Python eval kernel runs, since omp
# gives that kernel only an allow-list of variables (PATH, HOME, TMPDIR, XDG_* and a few more:
# packages/coding-agent/src/eval/py/runtime.ts in oh-my-pi). On sami-devbox on 2026-11-21 at 03:4xZ
# three `go test` runs under an eval kernel had no GOCACHE, ~/.cache/go-build held 475 GB and
# ~/go/pkg/mod 11 GB. Every go command reads this file for a variable its environment does not
# have, so those commands now use the caches devbox/gocache-cap and devbox/nvme-cache-cap hold. A
# variable a process has still wins, so the shells, the agent sessions and an agent box (whose
# ~/.config is its own) keep theirs.
#
# The values are the ones .bashrc sets: GOCACHE=<scratch>/gocache, where <scratch> is
# /mnt/scratch/<user> when devbox/gce-disks made it and /mnt/nvme/<user> otherwise, and
# GOMODCACHE=/mnt/nvme/<user>/gomodcache. Every other line of the file is kept. A machine without
# /mnt/nvme/<user> is left alone, and so is a GOENV of "off", which turns the file off.
#
# GO_ENV_NVME_HOME and GO_ENV_SCRATCH_HOME replace /mnt/nvme/<user> and /mnt/scratch/<user>, so a
# test can run this against a scratch tree.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

user="$(id -un)"
nvme_home="${GO_ENV_NVME_HOME:-/mnt/nvme/$user}"
scratch_home="${GO_ENV_SCRATCH_HOME:-/mnt/scratch/$user}"
if [ ! -d "$nvme_home" ]; then
    echo "go-env: no $nvme_home, so Go's env file is left alone"
    exit 0
fi
build_cache="$nvme_home/gocache"
if [ -d "$scratch_home" ]; then build_cache="$scratch_home/gocache"; fi
module_cache="$nvme_home/gomodcache"

env_file="${GOENV:-${XDG_CONFIG_HOME:-$HOME/.config}/go/env}"
if [ "$env_file" = off ]; then
    echo "go-env: GOENV is off, so Go reads no env file and none is written"
    exit 0
fi

old=""
if [ -f "$env_file" ]; then old="$(cat "$env_file")"; fi
# value_of KEY: the value the file gives KEY now (Go takes the last line naming it), or "unset".
value_of() {
    local value
    value="$(printf '%s\n' "$old" | grep -E "^$1=" | tail -n 1)" || true
    if [ -n "$value" ]; then echo "${value#*=}"; else echo unset; fi
}
kept="$(printf '%s\n' "$old" | grep -v -E '^(GOCACHE|GOMODCACHE)=' | grep -v '^$')" || true
new="${kept:+$kept
}GOCACHE=$build_cache
GOMODCACHE=$module_cache"
if [ "$new" = "$old" ]; then
    echo "go-env: $env_file already names GOCACHE=$build_cache and GOMODCACHE=$module_cache"
    exit 0
fi

mkdir -p "$(dirname "$env_file")"
tmp="$(mktemp "$env_file.XXXXXX")"
printf '%s\n' "$new" >"$tmp"
mv "$tmp" "$env_file"
echo "go-env: $env_file: GOCACHE $(value_of GOCACHE) -> $build_cache, GOMODCACHE $(value_of GOMODCACHE) -> $module_cache"
