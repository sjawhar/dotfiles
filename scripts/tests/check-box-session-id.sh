#!/usr/bin/env bash
# Does box_session_id name the occupant of a live box? Against the real boxes that failed.
#
# GRADE (02:03Z) and the librarian lane (02:35Z) both had a live omp and a cgroup naming their
# box, and `agentbox restart <session id>` answered "no running box matches" for each. The
# control is a box launched with a brief and never resumed: it carries no session id anywhere
# the host can read, and must still answer EMPTY rather than guess.
set -uo pipefail
script="${1:?usage: check-box-session-id.sh /path/to/scripts/agentbox}"

# Take the function out without running the script: sourcing agentbox runs a launcher.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
sed -n '/^box_session_id() {/,/^}/p' "$script" >"$tmp/fn.sh"
# shellcheck disable=SC1090
source "$tmp/fn.sh"

check() { # check BOX EXPECTED_SESSION_OR_EMPTY WHAT
    local box="$1" want="$2" what="$3" got
    got="$(box_session_id "$box" 2>/dev/null)"
    if [[ "$got" == "$want" ]]; then
        printf 'ok    %-26s %s\n' "$what" "${got:-(empty, as it must be)}"
    else
        printf 'WRONG %-26s got %-38s want %s\n' "$what" "${got:-(empty)}" "${want:-(empty)}"
    fi
}

check agentbox-b6e2854c5c5b 01a13325-a08c-7572-acb7-c79d1750edd5 "GRADE, failed at 02:03Z"
check agentbox-647ab4d8d39d 01a14ba2-817c-748e-9cb5-d1ad60308a9c "librarian, failed 02:35Z"
# A box launched with a brief and never resumed still names its session, because its omp holds
# its OWN transcript open for writing: pid 1888 in this box holds
# 2026-11-07T23-04-30-025Z_01a1b841-....jsonl with flags 02102001, and the box's creation stamp
# agrees. An earlier version of this check expected empty here, which was my assumption rather
# than a reading.
check agentbox-5af0edb69a02 01a1b841-9c49-741e-9cdd-958294b11afb "fresh-brief box (FAMS)"
check agentbox-does-not-exist "" "a box that is not there"
