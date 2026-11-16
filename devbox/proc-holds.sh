# shellcheck shell=bash
# shellcheck disable=SC2154  # $deadline is the caller's, and every function here reads it
# proc-holds.sh: which processes hold a path, read from /proc. Sourced, never run, by
# devbox/tmp-stale-reap and devbox/nvme-cache-cap, so the cleaners here share one rule for "a
# running process may still read this".
#
# A process holds a path when the path, or a path under it, is one of its open files, its cwd, exe
# or root, a file it has mapped, one of its command-line arguments, or the value of an environment
# variable the caller names. Only this user's fd, maps and environ are readable, so another user's
# process holding a path that way is invisible here; a caller covers that some other way (an owner
# gate, an age window).
#
# Each kind is one read of /proc for every path at once, never one per path. The /proc entries go
# to find and grep on stdin, never as arguments: at 22,765 processes on sami-devbox on 2026-11-16
# the four fd, cwd, exe and root globs alone were over the 2 MB argument limit, so that exec failed
# with "Argument list too long", exit 126, and the scan saw nothing while reading as finished.
#
# Every read runs as a stream under the caller's $deadline (epoch seconds): a read of a process's
# maps, cmdline or environ waits on that process's memory lock, which a process stuck in a page
# fault on a stalled disk holds.

# stream LIMIT DELIM CMD...: what CMD writes, under a hard limit of LIMIT seconds, then one last
# record `\1end<TAB>STATUS` ended by DELIM, so that next_record can tell a finished listing from
# one cut short. It runs as the process substitution a loop reads.
stream() {
    local limit=$1 delim=$2 rc=0
    shift 2
    exec 2>/dev/null
    if ((limit > 0)); then
        timeout -s KILL "$limit" "$@" || rc=$?
    else
        rc=137
    fi
    printf '\1end\t%d%b' "$rc" "$delim"
}

# next_record DELIM: the next record of the stream on stdin, in $record, read within $deadline.
# It fails at the stream's last record, with that stream's status in $stream_end, and at the
# deadline, with "deadline" there.
next_record() {
    local wait=$((deadline - EPOCHSECONDS)) rc=0
    stream_end=deadline
    ((wait > 0)) || return 1
    IFS= read -r -d "$1" -t "$wait" record || rc=$?
    if ((rc != 0)); then
        # Past 128 the read timed out; below it the stream ended without its last record.
        ((rc > 128)) || stream_end=lost
        return 1
    fi
    if [[ "$record" == $'\1end\t'* ]]; then
        stream_end="${record#*$'\t'}"
        return 1
    fi
}

# proc_holds_read DONE...: after a read's loop, 0 when it finished with one of the DONE statuses.
# Otherwise 1, with $stream_end saying why: "deadline", "lost" (the read ended without its last
# record), or the status the read exited with.
proc_holds_read() {
    local want
    if ((EPOCHSECONDS >= deadline)); then
        stream_end=deadline
        return 1
    fi
    for want in "$@"; do
        [[ "$stream_end" == "$want" ]] && return 0
    done
    return 1
}

# proc_holds PROC ROOT CALLBACK [VAR...]: call CALLBACK PID HOW PATH for every path at or under
# ROOT that a process in PROC holds. HOW is "an open file", "cwd", "exe", "root", "a mapped file",
# "an argument", or the VAR whose value names the path. Returns 0 once every read has finished;
# otherwise 1, with $stream_end saying why, and what it found before then has gone to CALLBACK.
# A caller treats a 1 as "unknown", and removes nothing on an unknown.
#
# A read finishes with its tool's own status: find's 1 and xargs' 123 (a grep that found nothing,
# or a process that exited while it was read) are as finished as a 0. When this script is itself
# in PROC, as it is in the real /proc, the first read must have seen it: a scan blind to its own
# process saw nothing at all.
proc_holds() {
    local proc="$1" root="$2" callback="$3" link target pid kind value saw_self="" vars
    shift 3
    while next_record $'\n'; do
        link="${record%%$'\t'*}"
        target="${record#*$'\t'}"
        pid="${link#"$proc"/}"
        pid="${pid%%/*}"
        [[ "$pid" == "$$" ]] && saw_self=1
        [[ "$target" == "$root" || "$target" == "$root"/* ]] || continue
        kind="${link##*/}"
        [[ "$kind" =~ ^[0-9]+$ ]] && kind="an open file"
        "$callback" "$pid" "$kind" "$target"
    done < <(printf '%s\0' "$proc"/[0-9]*/fd "$proc"/[0-9]*/cwd "$proc"/[0-9]*/exe "$proc"/[0-9]*/root |
        stream "$((deadline - EPOCHSECONDS))" '\n' \
            find -files0-from - -maxdepth 1 -ignore_readdir_race -type l -printf '%p\t%l\n')
    proc_holds_read 0 1 || return 1
    if [[ -d "$proc/$$" && -z "$saw_self" ]]; then
        stream_end="blind: the scan of $proc did not list this process ($$)"
        return 1
    fi

    while next_record $'\n'; do
        pid="${record#"$proc"/}"
        pid="${pid%%/*}"
        "$callback" "$pid" "a mapped file" "${record#*:}"
    done < <(printf '%s\0' "$proc"/[0-9]*/maps |
        stream "$((deadline - EPOCHSECONDS))" '\n' xargs -0 grep -Ho -- "${root}/[^[:space:]]*")
    proc_holds_read 0 123 || return 1

    while next_record ''; do
        pid="${record#"$proc"/}"
        pid="${pid%%/*}"
        value="${record#*:}"
        [[ "$value" == "$root" || "$value" == "$root"/* ]] || continue
        "$callback" "$pid" "an argument" "$value"
    done < <(printf '%s\0' "$proc"/[0-9]*/cmdline |
        stream "$((deadline - EPOCHSECONDS))" '\0' xargs -0 grep -zaoH -- "${root}\(/.*\)\?$")
    proc_holds_read 0 123 || return 1

    (($#)) || return 0
    vars="$(
        IFS='|'
        printf '%s' "$*"
    )"
    while next_record ''; do
        pid="${record#"$proc"/}"
        pid="${pid%%/*}"
        value="${record#*:}"
        "$callback" "$pid" "${value%%=*}" "${value#*=}"
    done < <(printf '%s\0' "$proc"/[0-9]*/environ |
        stream "$((deadline - EPOCHSECONDS))" '\0' xargs -0 grep -zaoH -E -- "(${vars})=${root}/[^[:space:]]*")
    proc_holds_read 0 123
}
