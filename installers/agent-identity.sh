#!/bin/bash
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# installers/agent-identity.sh [ACCOUNT]: the Google account every client on this Google VM acts as,
# when it is not the VM's own attached account. Role installer: devbox/install.sh runs it with no
# argument, which changes nothing until the switch is on. Idempotent; prints each change it makes.
#
# The switch is the file ~/.config/agent-identity/account. While it names a service account that is
# not the VM's own, the metadata relay (scripts/agentbox-gce-metadata-relay, its header has the
# details) answers every token, identity-token and email request as that account, minted through
# IAM Credentials on the VM's own token. With no file, or with the VM's own account in it, every
# client reads the VM's own account, as before. `agent-identity.sh <account>` writes the file; the
# revert is to run it with the VM's own account.
#
# While the file exists, a run makes sure that:
#   - the host's relay runs: devbox/agent-identity-relay.service, on RELAY (127.0.0.1:8170), from
#     boot whether or not an agent box has started. A unit someone disabled and stopped stays
#     stopped, and the run fails, since no host client reads the account without it;
#   - the relay serves the account's email and mints its token. Before the account's Token Creator
#     grant applies, the mint is refused: the run then fails before it points any client at the
#     relay, and says why;
#   - a running agentbox-imds, the agent boxes' relay that scripts/agentbox starts, is restarted
#     when the account differs from ~/.config/agent-identity/applied, the last successful run
#     (scripts/agentbox already points every box at it);
#   - the host's clients read the relay, through GCE_METADATA_HOST (scripts, google-auth, Go),
#     GCE_METADATA_ROOT (gcloud) and GCE_METADATA_IP (google-auth's probe): the systemd user units
#     from ~/.config/environment.d/50-gce-metadata.conf and the user manager's environment, since
#     they read no shell file; every bash from the same file, which .bashrc's environment zone
#     reads (a login shell through ~/.profile, an interactive one, and a command sshd runs, which
#     reads no ~/.profile); and a pane opened later in a running tmux server from its global
#     environment;
#   - gcloud's configuration `box` names the account the relay serves, in ~/.config/gcloud and in the
#     boxid config dir: installers/gcloud-box.sh and installers/gcloud-boxid.sh run again with the
#     three variables set.
# A running unit, shell or session keeps the environment it started with: restart each one after a
# run that changed the account. Such a run also removes the old account's tokens that clients keep
# outside gcloud's store (scripts/gcloud-token-fast's, gke-gcloud-auth-plugin's). A run that wrote
# the switch and then fails puts the previous one back, and runs the two gcloud installers again so
# configuration box names what the relay then serves; the clients keep reading the relay, which
# passes the VM's own account through.
#
# Tests: AGENTBOX_GCE_METADATA_UPSTREAM (the real metadata server, host:port, as the relay reads it)
# and AGENT_IDENTITY_RELAY (the host relay) name stand-ins; HOME a throwaway home.

usage="usage: installers/agent-identity.sh [SERVICE_ACCOUNT_EMAIL]"
DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RELAY="${AGENT_IDENTITY_RELAY:-127.0.0.1:8170}"
UPSTREAM="${AGENTBOX_GCE_METADATA_UPSTREAM:-169.254.169.254:80}"
ACCOUNT_FILE="$HOME/.config/agent-identity/account"
APPLIED_FILE="$HOME/.config/agent-identity/applied"
ENV_FILE="$HOME/.config/environment.d/50-gce-metadata.conf"
VARS=(GCE_METADATA_HOST GCE_METADATA_ROOT GCE_METADATA_IP)

case "${1:-}" in
    -h | --help) echo "$usage"; exit 0 ;;
esac
(( $# <= 1 )) || { echo "$usage" >&2; exit 2; }
account="${1:-}"
if [[ -n "$account" && ! "$account" =~ ^[^@[:space:]]+@[^@[:space:]]+\.gserviceaccount\.com$ ]]; then
    echo "agent-identity: '$account' is not a service account email; $usage" >&2
    exit 2
fi

# The real metadata server, not GCE_METADATA_HOST: after the switch that names the relay.
if ! GCE_METADATA_HOST="$UPSTREAM" "$DOTFILES_DIR/scripts/on-google-vm"; then
    echo "agent-identity: not a Google VM; nothing changed."
    exit 0
fi

# Refuse a box before writing the shared switch, not only before changing the host's units.
if [[ -n "$account" || -s "$ACCOUNT_FILE" ]] &&
    { [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; }; then
    echo "agent-identity: this is an agent box; the host's units and shells are not here. Run it from a host shell." >&2
    exit 1
fi

old="$(tr -d '[:space:]' 2>/dev/null <"$ACCOUNT_FILE" || true)"
changed=no
if [[ -n "$account" && "$account" != "$old" ]]; then
    mkdir -p "$(dirname "$ACCOUNT_FILE")"
    # Relays read this file on every request. Restore the previous switch if any later step fails,
    # and then make configuration box name what the relay serves again.
    backup="$(mktemp "${ACCOUNT_FILE}.previous.XXXXXX")"
    had_account=no
    gcloud_touched=no
    if [[ -e "$ACCOUNT_FILE" ]]; then
        cp -p "$ACCOUNT_FILE" "$backup"
        had_account=yes
    fi
    rollback_switch() {
        if [[ "$had_account" == yes ]]; then
            mv -f "$backup" "$ACCOUNT_FILE"
        else
            rm -f "$ACCOUNT_FILE" "$backup"
        fi
        echo "agent-identity: the run failed, so $ACCOUNT_FILE is restored (${old:-none})" >&2
        if [[ "$gcloud_touched" == yes ]]; then
            bash "$DOTFILES_DIR/installers/gcloud-box.sh" >&2 || true
            bash "$DOTFILES_DIR/installers/gcloud-boxid.sh" >&2 || true
        fi
    }
    trap rollback_switch EXIT
    printf '%s\n' "$account" >"$ACCOUNT_FILE.tmp.$$"
    mv -f "$ACCOUNT_FILE.tmp.$$" "$ACCOUNT_FILE"
    echo "agent-identity: $ACCOUNT_FILE: ${old:-none} -> $account"
    changed=yes
fi
if [[ ! -s "$ACCOUNT_FILE" ]]; then
    echo "agent-identity: no $ACCOUNT_FILE, so every client here acts as the VM's own account; nothing changed."
    exit 0
fi
account="$(tr -d '[:space:]' <"$ACCOUNT_FILE")"
applied="$(cat "$APPLIED_FILE" 2>/dev/null || true)"


# 1. The relays. A host relay unit that is linked, disabled and inactive was stopped on purpose
#    (scripts/AGENTS.md, "Acting on the system"): leave it stopped. The switch cannot work without
#    it, so the run fails, and the trap above restores a switch this run wrote.
was_active=no
systemctl --user is-active --quiet agent-identity-relay.service && was_active=yes
unit="$HOME/.config/systemd/user/agent-identity-relay.service"
if [[ -e "$unit" || -L "$unit" ]] && [[ "$was_active" == no ]] \
    && ! systemctl --user is-enabled --quiet agent-identity-relay.service; then
    echo "agent-identity: agent-identity-relay is disabled and inactive, so someone stopped it; keeping it stopped." \
        "No host client reads $account without it: systemctl --user enable --now agent-identity-relay.service, then run this again." >&2
    exit 1
fi
arm_user_service devbox agent-identity-relay || exit 1
if [[ "$account" != "$applied" && "$was_active" == yes ]]; then
    systemctl --user restart agent-identity-relay.service
    echo "agent-identity: restarted agent-identity-relay"
fi
served=""
for _ in $(seq 1 20); do
    served="$(curl -fsS -m 2 --noproxy '*' -H 'Metadata-Flavor: Google' \
        "http://$RELAY/computeMetadata/v1/instance/service-accounts/default/email" 2>/dev/null)" \
        && [[ "$served" == "$account" ]] && break
    sleep 0.5
done
[[ "$served" == "$account" ]] || {
    echo "agent-identity: the relay at $RELAY serves '${served:-nothing}', not $account (journalctl --user -u agent-identity-relay)" >&2
    exit 1
}
# The relay must also mint the account's tokens: a switch to an account whose tokens it cannot mint
# (no Token Creator grant on it yet) would break every client on the host. A refusal's body names it
# and never holds a token; a 200's body holds a token, so it is never printed.
minted="$(curl -sS -m 30 --noproxy '*' -H 'Metadata-Flavor: Google' -w '\n%{http_code}' \
    "http://$RELAY/computeMetadata/v1/instance/service-accounts/default/token" 2>&1 || true)"
if [[ "${minted##*$'\n'}" != 200 ]]; then
    echo "agent-identity: the relay at $RELAY mints no token of $account, so no client is switched: $(sed '$d' <<<"$minted" | tail -1 | cut -c1-400)" >&2
    exit 1
fi
unset minted

# The account works through the host's relay: now the agent boxes' relay restarts, so it runs the
# installed relay, and the old account's cached tokens go.
if [[ "$account" != "$applied" ]]; then
    if systemctl --user is-active --quiet agentbox-imds; then
        systemctl --user restart agentbox-imds
        echo "agent-identity: restarted agentbox-imds, the agent boxes' relay, so it serves $account"
    fi
    # Tokens of the old account that a client keeps outside gcloud's store, each up to an hour:
    # scripts/gcloud-token-fast's and gke-gcloud-auth-plugin's. Each is minted again on its next use.
    for cache in "${XDG_RUNTIME_DIR:-/tmp}/gcloud-token/access-token" "$HOME/.kube/gke_gcloud_auth_plugin_cache"; do
        if [[ -e "$cache" ]]; then
            rm -f "$cache"
            echo "agent-identity: removed $cache, a token of the old account"
        fi
    done
fi

# 2. The systemd user units and every bash (.bashrc reads the same file): environment.d, and the
#    user manager now.
want="$(printf '%s\n' "${VARS[@]/%/=$RELAY}")"
if [[ "$(cat "$ENV_FILE" 2>/dev/null)" != "$want" ]]; then
    mkdir -p "$(dirname "$ENV_FILE")"
    printf '%s\n' "$want" >"$ENV_FILE.tmp.$$"
    mv -f "$ENV_FILE.tmp.$$" "$ENV_FILE"
    echo "agent-identity: $ENV_FILE -> the relay $RELAY"
fi
manager="$(systemctl --user show-environment 2>/dev/null || true)"
for line in "${VARS[@]/%/=$RELAY}"; do
    if ! grep -qxF "$line" <<<"$manager"; then
        systemctl --user set-environment "${VARS[@]/%/=$RELAY}"
        echo "agent-identity: user manager environment ${VARS[*]} -> $RELAY"
        break
    fi
done

# 3. tmux's global environment, for a pane opened later in a running server (.bashrc's socket).
export TMUX_TMPDIR="${TMUX_TMPDIR:-$HOME/.tmux/sockets}"
if command -v tmux >/dev/null && tmux has-session 2>/dev/null; then
    for name in "${VARS[@]}"; do
        if [[ "$(tmux show-environment -g "$name" 2>/dev/null)" != "$name=$RELAY" ]]; then
            tmux set-environment -g "$name" "$RELAY"
            echo "agent-identity: tmux global environment $name -> $RELAY"
        fi
    done
else
    echo "agent-identity: no tmux server runs; a new one's panes read .bashrc"
fi

# 4. gcloud's configuration `box`, read from the relay, in ~/.config/gcloud and in the boxid dir.
export GCE_METADATA_HOST="$RELAY" GCE_METADATA_ROOT="$RELAY" GCE_METADATA_IP="$RELAY"
gcloud_touched=yes
bash "$DOTFILES_DIR/installers/gcloud-box.sh"
bash "$DOTFILES_DIR/installers/gcloud-boxid.sh"

# The account this run applied, then, with every step held, the switch it wrote is kept.
if [[ "$account" != "$applied" ]]; then
    printf '%s\n' "$account" >"$APPLIED_FILE.tmp.$$"
    mv -fT "$APPLIED_FILE.tmp.$$" "$APPLIED_FILE"
fi
if [[ "$changed" == yes ]]; then
    trap - EXIT
    rm -f "$backup"
fi

echo "agent-identity: clients on this host act as $account through the relay $RELAY; restart each running shell, unit and session to pick it up"
