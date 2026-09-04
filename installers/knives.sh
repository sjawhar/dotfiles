#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# knives - the binary is mise-managed ("github:sjawhar/knives" in mise.toml).
# This installer wires the fork registry. Everything else under
# ~/.config/knives (ledger/, state.json, seen.json, hook-sessions/) is
# per-machine state knives writes itself and stays out of version control.
#
# repos.toml names repositories by their upstream remote, not by path, so one
# registry serves every machine: knives finds each checkout by its remotes and
# reports one that is absent here as `not on this machine`. `knives register`
# prints a paste-ready entry for the checkout you stand in.
mkdir -p ~/.config/knives
ensure_link "${DOTFILES_DIR}/knives/repos.toml" ~/.config/knives/repos.toml
