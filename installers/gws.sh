#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# gws-accounts reads its account registry here; the wrapper itself is the omp
# plugin pin, linked as shims/gws.
mkdir -p ~/.config/gws
ensure_link "${DOTFILES_DIR}/gws/accounts.json" ~/.config/gws/accounts.json
