#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# gws-accounts reads its account registry here; shims/gws execs the omp plugin
# pin and directs `bun install` in omp/plugins when that package is absent.
mkdir -p ~/.config/gws
ensure_link "${DOTFILES_DIR}/gws/accounts.json" ~/.config/gws/accounts.json
