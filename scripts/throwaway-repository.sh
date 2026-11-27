# shellcheck shell=bash disable=SC2034 # the variables this sets are for the script that sources it
# throwaway-repository.sh: the one test of a throwaway repository, sourced by scripts/omp-session-trailer, which
# leaves the Omp-Session trailer off a commit in one, and by shims/git, whose `git commit` runs without the leak-term
# hooks in one. One copy, so the two cannot drift.
#
# A throwaway repository is one a test makes, with mkdtemp or a clone of another it made. Its common directory,
# resolved through symlinks, lies under a temporary root by whole path components (/tmpfoo is not under /tmp): $TMPDIR,
# /tmp, /var/tmp, /mnt/nvme/<user>/session-tmp or /mnt/scratch/<user>/session-tmp (sami-devbox). AND every URL of
# every remote, fetch and push, as git uses it (after insteadOf and pushInsteadOf), is an absolute local path, or a
# file:// URL of one, that resolves to a path under a temporary root: a test fixture's clone of its own bare
# `origin.git` is one (dispatch://INFRA-4003). A remote with a network URL, a remote helper's (`<transport>::<address>`,
# remote.<name>.vcs), a relative path (the name of a remote with no URL), or a path that does not exist makes it no
# throwaway one. So its commits reach only another repository under a temporary root but by a push to a URL, and
# shims/git scans every push to a public or unreadable GitHub repository. A new repository under ~/src is not one, and
# nor is a /tmp worktree of a repository with a GitHub remote: its common directory and its remotes are that
# repository's. A repository this cannot read is not one. shims/git's prune_is_safe asks another question (where no
# other session can register a worktree), with roots of its own and no remote test.
#
# throwaway_repository GIT [GIT_OPTION…] succeeds when the repository `GIT [GIT_OPTION…]` works in is a throwaway one.
# It sets throwaway to why: `yes`; `remote` (a remote is not a local path under a temporary root); `remote-unread` (git
# cannot list the remotes or their URLs); `common-unread` (the common directory cannot be read); or `no-root`. It sets
# throwaway_remotes to `no remote` or `only remotes under a temporary root` for `yes` and `no-root`, throwaway_common
# to the common directory, resolved, for those two, and throwaway_root to its temporary root for `yes`. It reads the
# remotes first, so a repository whose first remote is on GitHub costs two gits.
#
# throwaway_temporary_root PATH succeeds when PATH, resolved through symlinks, lies under a temporary root, and sets
# throwaway_found to that root.
#
# The caller's errexit does not reach a function called in a condition, so each step here tests its own status.

throwaway="" throwaway_remotes="" throwaway_common="" throwaway_root="" throwaway_found="" throwaway_path=""
throwaway_roots=()
throwaway_roots_read=0

# throwaway_read_roots: throwaway_roots, each temporary root that exists, resolved through symlinks, read once. A root
# that resolves to / would take in every repository, so it is left out.
throwaway_read_roots() {
    local user root
    local -a resolved=()
    ((throwaway_roots_read)) && return 0
    throwaway_roots_read=1
    user="$(id -un 2> /dev/null)" || user=""
    # One realpath for every root: it prints each one that exists, NUL-terminated, and only complains of the others.
    mapfile -d '' -t resolved < <(realpath -ez -- ${TMPDIR:+"$TMPDIR"} /tmp /var/tmp \
        ${user:+"/mnt/nvme/$user/session-tmp" "/mnt/scratch/$user/session-tmp"} 2> /dev/null)
    for root in ${resolved[@]+"${resolved[@]}"}; do
        if [[ "$root" == /* && "$root" != / ]]; then throwaway_roots+=("$root"); fi
    done
    return 0
}

# throwaway_under_root PATH: succeeds when PATH, already resolved, lies under a temporary root; sets throwaway_found.
throwaway_under_root() {
    local root
    throwaway_found=""
    throwaway_read_roots
    for root in ${throwaway_roots[@]+"${throwaway_roots[@]}"}; do
        if [[ "$1" == "$root"/* ]]; then
            throwaway_found="$root"
            return 0
        fi
    done
    return 1
}

throwaway_temporary_root() {
    local path
    path="$(realpath -m -- "$1" 2> /dev/null)" || return 1
    throwaway_under_root "$path"
}

# throwaway_local_path URL: succeeds when git reads the remote URL as a local path, an absolute one or `file://` and
# one with no %-escape for git to decode, and sets throwaway_path to it. Every other URL fails: a network one
# (`<scheme>://…`, scp's `host:path`), a helper's `<transport>::<address>`, a relative path, a file:// URL with a host.
# A path that holds `::` or `://` fails too, although git would read it as a path.
throwaway_local_path() {
    local url="$1"
    throwaway_path=""
    if [[ "$url" == file://* ]]; then
        url="${url#file://}"
        [[ "$url" != *%* ]] || return 1
    fi
    [[ "$url" == /* && "$url" != *::* && "$url" != *://* ]] || return 1
    throwaway_path="$url"
}

throwaway_repository() {
    local remotes name push urls url code=0 path common
    local -a paths=() resolved=()
    throwaway="" throwaway_remotes="no remote" throwaway_common="" throwaway_root=""
    if ! remotes="$("$@" remote 2> /dev/null)"; then
        throwaway="remote-unread"
        return 1
    fi
    if [[ -n "$remotes" ]]; then
        while IFS= read -r name; do
            for push in "" --push; do
                if ! urls="$("$@" remote get-url ${push:+"$push"} --all -- "$name" < /dev/null 2> /dev/null)"; then
                    throwaway="remote-unread"
                    return 1
                fi
                while IFS= read -r url; do
                    if ! throwaway_local_path "$url"; then
                        throwaway="remote"
                        return 1
                    fi
                    paths+=("$throwaway_path")
                done <<< "$urls"
            done
        done <<< "$remotes"
        # A remote helper (remote.<name>.vcs) is handed the URL as an address of its own.
        "$@" config --get-regexp '^remote\..*\.vcs$' > /dev/null 2>&1 || code=$?
        case "$code" in
            0) throwaway="remote"; return 1 ;;
            1) ;;
            *) throwaway="remote-unread"; return 1 ;;
        esac
        # One realpath for every path: it prints each one that exists, so a count short of them is one that does not.
        mapfile -d '' -t resolved < <(realpath -ez -- "${paths[@]}" 2> /dev/null)
        if ((${#resolved[@]} != ${#paths[@]})); then
            throwaway="remote"
            return 1
        fi
        for path in "${resolved[@]}"; do
            if ! throwaway_under_root "$path"; then
                throwaway="remote"
                return 1
            fi
        done
        throwaway_remotes="only remotes under a temporary root"
    fi
    if ! common="$("$@" rev-parse --path-format=absolute --git-common-dir 2> /dev/null)" ||
        ! common="$(realpath -e -- "$common" 2> /dev/null)"; then
        throwaway="common-unread"
        return 1
    fi
    throwaway_common="$common"
    if throwaway_under_root "$common"; then
        throwaway_root="$throwaway_found"
        throwaway="yes"
        return 0
    fi
    throwaway="no-root"
    return 1
}
