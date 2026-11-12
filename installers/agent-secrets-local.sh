#!/bin/bash
set -euo pipefail

# Read env.local itself rather than broker-url.sh: that script also loads the committed broker.env,
# which names another broker.
env_local="${HOME}/.config/agent-secrets/env.local"
url=""
if [ -f "$env_local" ]; then
    # shellcheck disable=SC2016 # The child shell expands this command string.
    url="$(env -u AGENT_SECRETS_URL -u DOTFILES_AGENT_SECRETS_URL bash -c '. "$1" >/dev/null 2>&1; printf "%s" "${AGENT_SECRETS_URL-}"' agent-secrets-local "$env_local")" || url=""
fi

if [ -z "$url" ]; then
    echo "agent-secrets: ~/.config/agent-secrets/env.local sets no AGENT_SECRETS_URL; the helper unit is not installed or changed"
    exit 0
fi

# Drop the restart-over-sessions override, so this path never restarts a helper that has registered
# sessions. To restart over them anyway, run it directly:
#   AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1 bash ~/.dotfiles/installers/agent-secrets.sh
exec env -u AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/agent-secrets.sh"
