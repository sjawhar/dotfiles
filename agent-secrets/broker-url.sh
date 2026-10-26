# shellcheck shell=sh
# Sourced, never run: exports AGENT_SECRETS_URL, the secrets broker's address (AGENTC-393), for
# .bashrc (bash or zsh), scripts/agent-secrets-session, scripts/secret-run,
# scripts/agent-secrets-login and scripts/agentbox. The rule is written here and nowhere else.
#
# broker.env holds the committed URL, and ~/.config/agent-secrets/env.local (optional, uncommitted)
# overrides it on one machine. A URL loaded from them is recorded in DOTFILES_AGENT_SECRETS_URL,
# and a process that inherits a URL equal to that record loads them again. So a changed broker.env
# reaches every new shell and launcher, including those under a tmux server started before the
# change. Any other URL the caller has wins: one it chose itself, such as the local broker Legion's
# dev-broker.sh exports, never matches the record, even in a shell whose own load set the record.
# The record has no AGENT_SECRETS_ prefix: the agent-secrets exec form drops every such variable
# from its child's environment except the session's own three.
if [ -z "${AGENT_SECRETS_URL:-}" ] || [ "$AGENT_SECRETS_URL" = "${DOTFILES_AGENT_SECRETS_URL:-}" ]; then
    # shellcheck source=SCRIPTDIR/broker.env
    . "${DOTFILES_DIR:-$HOME/.dotfiles}/agent-secrets/broker.env"
    # shellcheck source=/dev/null
    [ ! -f "$HOME/.config/agent-secrets/env.local" ] || . "$HOME/.config/agent-secrets/env.local"
    DOTFILES_AGENT_SECRETS_URL="${AGENT_SECRETS_URL:-}"
    export DOTFILES_AGENT_SECRETS_URL
fi
export AGENT_SECRETS_URL
