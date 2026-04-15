#!/usr/bin/env bash
# Claude Code statusline: active account + live subscription usage.
#
# Reads the OAuth access token from the active $CLAUDE_CONFIG_DIR and queries
# https://api.anthropic.com/api/oauth/usage (the same endpoint /status uses).
# Caches the response for 60s to avoid one HTTP call per turn.

set -u

read -r _stdin || true

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
token_file="$config_dir/.credentials.json"
# Identity comes from .claude.json's oauthAccount so that mid-session
# credentials swaps (pool rotation) update the label. Falls back to dir
# basename if .claude.json is missing/unreadable.
account=$(jq -r '.oauthAccount.displayName // .oauthAccount.emailAddress // empty' \
    "$config_dir/.claude.json" 2>/dev/null)
account="${account:-${config_dir##*/}}"
cache="/tmp/claude-usage.${account}.$(id -u).json"

dim=$'\033[2m'; reset=$'\033[0m'
green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'
color_for() {
    local pct_int
    pct_int=$(printf '%.0f' "$1" 2>/dev/null || echo 0)
    if   (( pct_int >= 90 )); then printf '%s' "$red"
    elif (( pct_int >= 70 )); then printf '%s' "$yellow"
    else                            printf '%s' "$green"
    fi
}

if [[ ! -s $cache ]] || [[ -z $(find "$cache" -mmin -1 2>/dev/null) ]]; then
    if [[ -r $token_file ]]; then
        token=$(jq -r '.claudeAiOauth.accessToken // empty' "$token_file" 2>/dev/null)
        if [[ -n ${token:-} ]]; then
            curl -sS -m 3 --fail \
                -H "Authorization: Bearer $token" \
                -H "anthropic-beta: oauth-2025-04-20" \
                "https://api.anthropic.com/api/oauth/usage" \
                -o "$cache.tmp" 2>/dev/null && mv "$cache.tmp" "$cache"
            rm -f "$cache.tmp"
        fi
    fi
fi

if [[ ! -s $cache ]] || ! jq -e 'has("five_hour")' "$cache" >/dev/null 2>&1; then
    printf '%s[%s] usage unavailable%s\n' "$dim" "$account" "$reset"
    exit 0
fi

# null utilization means at/past limit — render as 100 so the threshold check
# below fires and kicks off auto-rotation.
read -r five seven over_raw over_on < <(jq -r '
  [
    (.five_hour.utilization // 100),
    (.seven_day.utilization // 100),
    (.extra_usage.used_credits // 0),
    (if (.extra_usage.is_enabled // false) then 1 else 0 end)
  ] | @tsv
' "$cache")

five_c=$(color_for "$five")
seven_c=$(color_for "$seven")

over_str=""
if [[ $over_on == 1 ]]; then
    # used_credits appears to share units with overageCreditGrantCache.amount_minor_units (cents).
    over_dollars=$(awk -v c="$over_raw" 'BEGIN { printf "%.0f", c/100 }')
    if (( $(printf '%.0f' "$over_raw") > 0 )); then
        over_str=" ${dim}·${reset} ${yellow}\$${over_dollars} over${reset}"
    fi
fi

# Auto-rotation: when active account is at 5h≥100% or 7d≥100%, kick off a
# background rotation. cco __rotate takes a flock so concurrent renders
# don't race. Fire-and-forget; statusline returns without blocking.
five_int=$(printf '%.0f' "$five" 2>/dev/null || echo 0)
seven_int=$(printf '%.0f' "$seven" 2>/dev/null || echo 0)
if (( five_int >= 100 || seven_int >= 100 )); then
    cco_bin="${DOTFILES_DIR:-$HOME/.dotfiles}/scripts/cco"
    [[ -x "$cco_bin" ]] && "$cco_bin" __rotate </dev/null >/dev/null 2>&1 &
fi

printf '%s[%s]%s %s%.0f%%%s 5h %s·%s %s%.0f%%%s 7d%s\n' \
    "$dim" "$account" "$reset" \
    "$five_c" "$five" "$reset" \
    "$dim" "$reset" \
    "$seven_c" "$seven" "$reset" \
    "$over_str"
