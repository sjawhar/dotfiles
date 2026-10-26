# shellcheck shell=bash
# agent-spend-env.sh: the model-spend environment of one agent launch, sourced by scripts/agent-session and by
# shims/claude and shims/codex. `agent_spend_env AGENT [AGENT ARGS…]` exports it; AGENT is the harness's name (omp,
# claude, codex), the args are the ones the harness gets (read for a resumed session's id). It never exits the caller.
#
# theorem-labs/agent-infra spend/SETUP.md, dispatch://INFRA-165: once agent-spend/endpoint exists and this machine
# holds the collector's token (installers/agent-spend.sh), every omp, Claude Code and Codex run exports one record per
# model call (tokens and the price the harness computes, no prompt or reply text), tagged with its project and
# agent-spend/person. A caller that set a different collector (an OSS Verify lane VM) keeps it and its tags. A caller
# that already points at this machine's own collector is another session's child (an `omp -p` or `claude -p` run from
# its bash tool or a script it started), so its tags are resolved again here and replace the inherited ones.
# The project is a COS dashboard stream (oss-verify, verified-linux, task-making, verified-grader, rocq-speed,
# lean-speed, legion, infra-monitoring, agent-process, omp, pivot, shared), and it sticks to the session:
# ~/.config/agent-spend/sessions/<session-id> holds `project[ subproject]`, written by
# omp/extensions/session-project.ts when an omp session starts and here when a launch names THEOREM_PROJECT. First of:
# THEOREM_PROJECT (and THEOREM_SUBPROJECT), unless it equals the inherited theorem.project, which makes it this
# function's own export to a parent session; the file of the session this launch resumes (`--resume`, `-r`,
# `--restore`, by id, unique id prefix or transcript path); the file of the session that started this process
# (OMP_SESSION_ID, which omp/extensions/session-env.ts exports to its children); the theorem.project this process
# inherited; agent-spend/projects, which maps the working directory; the machine's
# ~/.config/agent-spend/default-project; `shared`. Whatever the source, an old project name becomes its COS stream
# (linux-verify, binary-equivalence, xv6, agent-infra), so no old name is written again. The result is exported as
# THEOREM_PROJECT and THEOREM_SUBPROJECT, so whatever the session starts inherits it.
# Each harness reads its own variables: omp sends its per-call record as a span, Claude Code as a log record (with
# CLAUDE_CODE_ENABLE_TELEMETRY), Codex as a log record through the exporter in ~/.codex/config.toml, which
# installers/agent-spend.sh writes (Codex takes only OTEL_RESOURCE_ATTRIBUTES from the environment). The exporter
# switches are set for every harness, since a Claude Code session's child omp would otherwise inherit traces off.
agent_spend_env() {
    local agent="${1##*/}"
    shift
    local dotfiles="${DOTFILES_DIR:-${HOME:-}/.dotfiles}"
    local conf="$dotfiles/agent-spend" local_dir="${XDG_CONFIG_HOME:-${HOME:-}/.config}/agent-spend"
    local token="$local_dir/token"
    [[ -s "$conf/endpoint" && -s "$token" ]] || return 0
    local endpoint
    endpoint="$(< "$conf/endpoint")"
    [[ -z "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" || "$OTEL_EXPORTER_OTLP_ENDPOINT" == "$endpoint" ]] || return 0

    local uuid='^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$'
    local sessions="$local_dir/sessions" session="" session_file="" session_id="" match
    local args=("$@") i
    for ((i = 0; i < ${#args[@]}; i++)); do
        case "${args[i]}" in
            --resume=* | --restore=*) session="${args[i]#*=}" ;;
            --resume | -r | --restore) session="${args[i + 1]:-}" ;;
        esac
    done
    # A transcript path's basename is <timestamp>_<uuid>.jsonl.
    session="${session##*/}" session="${session%.jsonl}" session="${session##*_}"
    if [[ "$session" =~ ^[0-9a-fA-F][0-9a-fA-F-]*$ ]]; then
        if [[ -f "$sessions/$session" ]]; then
            session_file="$sessions/$session"
        else
            local matches=()
            for match in "$sessions/$session"*; do
                [[ -f "$match" ]] && matches+=("$match")
            done
            (( ${#matches[@]} != 1 )) || session_file="${matches[0]}"
        fi
        if [[ -n "$session_file" ]]; then
            session_id="${session_file##*/}"
        elif [[ "$session" =~ $uuid ]]; then
            session_id="$session"
        fi
    fi
    # The inherited attributes, less the tags this function sets.
    local inherited="" inherited_project="" inherited_subproject="" entry entries=()
    IFS=',' read -ra entries <<< "${OTEL_RESOURCE_ATTRIBUTES:-}"
    for entry in ${entries[@]+"${entries[@]}"}; do
        case "$entry" in
            theorem.project=*) inherited_project="${entry#*=}" ;;
            theorem.subproject=*) inherited_subproject="${entry#*=}" ;;
            theorem.person=* | "") ;;
            *) inherited+="${inherited:+,}$entry" ;;
        esac
    done

    local explicit="" project="" subproject="" glob p s _
    [[ -z "${THEOREM_PROJECT:-}" || "$THEOREM_PROJECT" == "$inherited_project" ]] || explicit="$THEOREM_PROJECT"
    if [[ -n "$explicit" ]]; then
        project="$explicit" subproject="${THEOREM_SUBPROJECT:-}"
    elif [[ -n "$session_file" ]]; then
        read -r project subproject _ < "$session_file" || true
    elif [[ "${OMP_SESSION_ID:-}" =~ $uuid && -s "$sessions/${OMP_SESSION_ID:-}" ]]; then
        read -r project subproject _ < "$sessions/$OMP_SESSION_ID" || true
    elif [[ -n "$inherited_project" ]]; then
        project="$inherited_project" subproject="$inherited_subproject"
    fi
    if [[ -z "$project" && -r "$conf/projects" ]]; then
        while read -r glob p s; do
            [[ -z "$glob" || "$glob" == \#* ]] && continue
            # shellcheck disable=SC2053 # the glob is a pattern on purpose
            if [[ "$PWD" == $glob ]]; then project="$p" subproject="${s:-}"; break; fi
        done < "$conf/projects"
    fi
    if [[ -z "$project" && -s "$local_dir/default-project" ]]; then
        read -r project subproject _ < "$local_dir/default-project" || true
    fi
    case "$project" in
        linux-verify) project=verified-linux ;;
        binary-equivalence | xv6) project=task-making ;;
        agent-infra) project=legion ;;
        "") project=shared ;;
    esac
    if [[ -n "$explicit" && -n "$session_id" ]]; then
        # Atomic, so a reader never sees half a line; a failed write leaves the launch alone.
        local tmp="$sessions/.$session_id.$$.tmp"
        if ! { (umask 077 && mkdir -p "$sessions" \
            && printf '%s\n' "$project${subproject:+ $subproject}" > "$tmp") \
            && mv -f "$tmp" "$sessions/$session_id"; } 2> /dev/null; then
            rm -f "$tmp"
            echo "agent-spend: could not record $project for session $session_id in $sessions" >&2
        fi
    fi
    export THEOREM_PROJECT="$project"
    if [[ -n "$subproject" ]]; then export THEOREM_SUBPROJECT="$subproject"; else unset THEOREM_SUBPROJECT; fi
    local attrs="theorem.project=$project"
    [[ -z "$subproject" ]] || attrs+=",theorem.subproject=$subproject"
    [[ ! -s "$conf/person" ]] || attrs+=",theorem.person=$(< "$conf/person")"
    export OTEL_EXPORTER_OTLP_ENDPOINT="$endpoint"
    OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $(< "$token")"
    export OTEL_EXPORTER_OTLP_HEADERS
    export OTEL_RESOURCE_ATTRIBUTES="${inherited:+$inherited,}$attrs"
    export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf OTEL_METRICS_EXPORTER=none
    # omp's other logs carry local paths the collector would only drop.
    case "$agent" in
        claude) export CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_LOGS_EXPORTER=otlp OTEL_TRACES_EXPORTER=none ;;
        codex) unset OTEL_LOGS_EXPORTER OTEL_TRACES_EXPORTER CLAUDE_CODE_ENABLE_TELEMETRY ;;
        *) export OTEL_LOGS_EXPORTER=none; unset OTEL_TRACES_EXPORTER CLAUDE_CODE_ENABLE_TELEMETRY ;;
    esac
}
