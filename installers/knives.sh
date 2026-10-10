#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# knives - the binary is mise-managed ("github:sjawhar/knives" in mise.toml).
# This installer wires the fork registry and joins this machine to the shared
# notch ledger.
#
# repos.toml names repositories by their upstream remote, not by path, so one
# registry serves every machine: knives finds each checkout by its remotes and
# reports one that is absent here as `not on this machine`. `knives register`
# prints a paste-ready entry for the checkout you stand in.
mkdir -p ~/.config/knives
ensure_link "${DOTFILES_DIR}/knives/repos.toml" ~/.config/knives/repos.toml

# The ledger (~/.config/knives/ledger/<owner>/<name>/) travels between
# machines through the repositories each repos.toml entry's `ledger` names.
# knives sweeps it through every git directory in
# ~/.config/knives/ledger-repositories/, all using the ledger as their working
# tree, and commits as the machine each one's `knives.machine` names. Nothing
# else under ~/.config/knives (state.json, seen.json, hook-sessions/) leaves
# this machine.
#
# An agentbox mounts the host's ~/.config/knives and shares the host's ledger,
# so the setup belongs to the host alone. install.sh sources this file, so
# `return`, not `exit`, or the rest of install.sh would never run.
if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
    return 0 2>/dev/null || exit 0
fi

ledger_root="$HOME/.config/knives/ledger"
repositories="$HOME/.config/knives/ledger-repositories"
machine="$(hostname -s)"
mkdir -p "$ledger_root" "$repositories"

# Every distinct `ledger` value in the registry, one per line. A plain
# assignment, not `mapfile < <(...)`: set -e does not see a process
# substitution fail, and a parse failure here would leave this machine sharing
# nothing without a word.
ledgers="$(python3 - "${DOTFILES_DIR}/knives/repos.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as registry:
    repos = tomllib.load(registry).get("repos", {})
print("\n".join(sorted({entry["ledger"] for entry in repos.values() if "ledger" in entry})))
PY
)"

while IFS= read -r ledger; do
    [[ -n "$ledger" ]] || continue
    git_dir="$repositories/${ledger//\//--}.git"
    if [[ ! -d "$git_dir" ]]; then
        echo "knives: joining the ledger at $ledger"
        git init --quiet --bare "$git_dir"
        git --git-dir="$git_dir" config core.bare false
        git --git-dir="$git_dir" config core.worktree "$ledger_root"
        git --git-dir="$git_dir" remote add origin "https://github.com/$ledger"
    fi
    git --git-dir="$git_dir" config knives.machine >/dev/null ||
        git --git-dir="$git_dir" config knives.machine "$machine"
done <<<"$ledgers"
