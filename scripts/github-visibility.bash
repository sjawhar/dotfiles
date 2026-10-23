# github-visibility.bash: sourced by scripts/omp-session-trailer and scripts/omp-public-push,
# never run. It names the owners whose public GitHub repositories are published releases and
# asks GitHub whether one of their repositories is public.
#
# PUBLISHING_OWNERS: theorem-labs, whose public repositories (oss-verify, oss-verify-proofs,
# rocq, …) are its releases. Sami's own public repositories are not in it on purpose.
#
# publishing_slugs URL…  prints, once each, the lowercased owner/repo of every github.com URL
#                        whose owner PUBLISHING_OWNERS names; other URLs print nothing.
# github_visibility SLUG prints public, private, internal or unknown.
#
# Public is what an anonymous api.github.com request can read: 200 is public, 404 is not
# (private, internal, or gone). A rate-limited anonymous request (403/429) asks the GitHub App
# through `gh api` instead. An answer is cached under ~/.cache/omp/github-visibility/<owner>/<repo>:
# a private or internal one for 5 minutes, since a repository can be made public at any time; a
# public one for 30; an unknown one (offline, timeout, error) for 1, so a dead network costs one
# wait a minute rather than one per call. Each caller treats unknown as public.

PUBLISHING_OWNERS=(theorem-labs)

_github_url_re='^([a-z+]+://)?([^@/]+@)?(ssh\.)?github\.com(:443)?[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$'

publishing_slugs() {
    local url owner repo o seen=" "
    for url in "$@"; do
        [[ "$url" =~ $_github_url_re ]] || continue
        owner="${BASH_REMATCH[5],,}"
        repo="${BASH_REMATCH[6],,}"
        repo="${repo%.git}"
        for o in "${PUBLISHING_OWNERS[@]}"; do
            [[ "$owner" == "$o" && "$seen" != *" $owner/$repo "* ]] || continue
            seen+="$owner/$repo "
            echo "$owner/$repo"
        done
    done
}

github_visibility() {
    local slug="$1" at answer code
    local file="${XDG_CACHE_HOME:-$HOME/.cache}/omp/github-visibility/$slug"
    if read -r at answer 2> /dev/null < "$file"; then
        local ttl=60
        case "$answer" in
            public) ttl=1800 ;;
            private | internal) ttl=300 ;;
        esac
        if [[ "$at" =~ ^[0-9]+$ ]] && ((EPOCHSECONDS - at < ttl)); then
            echo "$answer"
            return
        fi
    fi
    code="$(curl -s -L -o /dev/null -w '%{http_code}' --max-time 2 \
        -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$slug" 2> /dev/null || true)"
    case "$code" in
        200) answer=public ;;
        404) answer=private ;;
        403 | 429)
            answer="$(timeout 3 gh api "repos/$slug" --jq .visibility 2> /dev/null < /dev/null || true)"
            [[ "$answer" == public || "$answer" == private || "$answer" == internal ]] || answer=unknown
            ;;
        *) answer=unknown ;;
    esac
    if mkdir -p "${file%/*}" 2> /dev/null; then
        printf '%s %s\n' "$EPOCHSECONDS" "$answer" > "$file.$$" 2> /dev/null && mv -f "$file.$$" "$file" 2> /dev/null || rm -f "$file.$$"
    fi
    echo "$answer"
}
