# shellcheck shell=bash
# agent-private.sh: the private-project check of one agent launch, sourced by scripts/agent-session and by
# shims/claude and shims/codex. `agent_private_check HARNESS CALLER [HARNESS ARGS…]` runs
# scripts/agent-private-check (its header has the rule) beside this file with the harness's name, the
# launcher's (CALLER: agent-session, or the shim's own name) and the arguments the harness gets. It returns
# 0 when the harness may start; else it says why on stderr, as CALLER, and returns 2. It changes nothing in
# the caller's environment.
agent_private_check() {
    local harness="$1" caller="$2" python=/usr/bin/python3 answer status=0
    shift 2
    # The system's python3 when there is one: a launch's PATH can hold none (a unit, a test's bare PATH)
    # or only mise's shim, which depends on HOME.
    [[ -x "$python" ]] || python=python3
    answer="$("$python" "${BASH_SOURCE[0]%/*}/agent-private-check" "$harness" "$caller" "$@" < /dev/null)" || status=$?
    if (( status == 0 )) && [[ "$answer" == start ]]; then
        return 0
    fi
    if [[ "$answer" == "refuse "* ]]; then
        echo "$caller: ${answer#refuse }" >&2
    else
        echo "$caller: scripts/agent-private-check gave no answer (exit $status), so $harness does not start; retry" >&2
    fi
    return 2
}
