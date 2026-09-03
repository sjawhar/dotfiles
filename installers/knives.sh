#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# knives - the binary is mise-managed ("github:sjawhar/knives" in mise.toml).
# This installer wires the fork registry. Everything else under
# ~/.config/knives (ledger/, state.json, seen.json, hook-sessions/) is
# per-machine state knives writes itself and stays out of version control.
#
# repos.toml paths are written with `~/` (knives expands it), so one registry
# serves every machine; a repo listed here but absent on this machine is
# simply reported as such by `knives status`. `knives register` prints
# absolute paths - rewrite to `~/` before pasting into knives/repos.toml.
mkdir -p ~/.config/knives
ensure_link "${DOTFILES_DIR}/knives/repos.toml" ~/.config/knives/repos.toml
