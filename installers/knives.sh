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
# An agent box mounts the host's ~/.config/knives and shares the host's
# ledger, so the setup belongs to the host alone; scripts/agentbox marks a box
# with AGENTBOX_BOX. Any other container (a devcontainer) has its own
# ~/.config/knives and joins like a host. install.sh sources this file last,
# so a failure here skips nothing else; `return`, not `exit`, for the box.
if [[ -n "${AGENTBOX_BOX:-}" ]]; then
    return 0 2>/dev/null || exit 0
fi

ledger_root="$HOME/.config/knives/ledger"
repositories="$HOME/.config/knives/ledger-repositories"
# A machine commits under its own ref, so its name must be one no other host
# uses: two hosts sharing a short hostname would each push a ref the other
# rejects as non-fast-forward. Suffix the hostname with the start of the
# machine id, which the OS fixes at install time.
if [[ -r /etc/machine-id ]]; then
    machine_id="$(cut -c1-8 /etc/machine-id)"
elif command -v ioreg >/dev/null; then
    machine_id="$(ioreg -rd1 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformUUID/ {print tolower(substr($4, 1, 8))}')"
else
    echo "knives: cannot name this machine: no /etc/machine-id and no ioreg" >&2
    exit 1
fi
[[ -n "$machine_id" ]] || { echo "knives: the machine id is empty" >&2; exit 1; }
machine="$(hostname -s)-$machine_id"
mkdir -p "$ledger_root" "$repositories"

# Every distinct `ledger` the registry names, one per line, in the one
# spelling knives keeps (lowercase `<owner>/<name>`, no `.git`), so two
# spellings of one repository make one git directory. `knives repos` exits 3
# when some fork has a problem and still prints the registry; any other exit
# stops the install. Plain assignments, not `mapfile < <(...)`: set -e does
# not see a process substitution fail.
repos_json="$(knives repos --json)" || [[ $? -eq 3 ]]
jq -e '.repos | type == "array"' <<<"$repos_json" >/dev/null 2>&1 || {
    echo "knives: \`knives repos --json\` printed no registry; is the knives on PATH older than the shared ledger?" >&2
    exit 1
}
ledgers="$(jq -r '[.repos[].ledger // empty] | unique | .[]' <<<"$repos_json")"

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

# Move a ledger an older knives wrote to the upstream-named layout, then bring
# in what other machines have pushed, so the first `knives notch` here reads
# the shared ledger. Migrate before the sweep: the sweep refuses a ledger
# still kept under registry keys, and migrate's own pull needs the git
# directories made above. Both are no-ops when there is nothing to do, so
# every install runs them.
#
# A failed migrate stops the install: until it succeeds every knives command
# here refuses. A failed sweep stops it only when it leaves entries this
# machine alone holds (`unsent`, per repository; absent when knives could not
# count them): a new machine whose git cannot reach GitHub yet has nothing to
# lose and joins on its next sweep.
if [[ -n "$ledgers" ]]; then
    knives ledger migrate || {
        status=$?
        echo "knives: the ledger migration did not finish; branch statements stay in state.json and every knives command refuses until it does. Once git can reach each ledger repository (gh auth login), run: bash ~/.dotfiles/installers/knives.sh" >&2
        exit "$status"
    }
    status=0
    sweep="$(knives ledger sweep --json)" || status=$?
    # A sweep that panicked or was killed prints no JSON: show what it printed,
    # and count its backlog as unknown rather than as nothing.
    summary="$(jq -c . <<<"$sweep" 2>/dev/null)" || summary="$sweep"
    echo "knives: ledger sweep: $summary"
    if [[ $status -ne 0 ]]; then
        stranded="$(jq '(.destinations // []) as $d | if ($d | length) == 0 then -1 else ([$d[] | .unsent // -1] | if any(. < 0) then -1 else add end) end' <<<"$sweep" 2>/dev/null)" || stranded=-1
        [[ -n $stranded ]] || stranded=-1
        if [[ $stranded -ne 0 ]]; then
            echo "knives: the sweep failed with entries written here that no ledger repository holds yet (unsent: $stranded, -1 = uncounted); fix the problems above, then run: knives ledger sweep" >&2
            exit "$status"
        fi
        echo "knives: this machine has joined the ledger but could not sync it yet, and has nothing waiting to go out. Once git can reach GitHub (gh auth login), run: knives ledger sweep" >&2
    fi
fi
