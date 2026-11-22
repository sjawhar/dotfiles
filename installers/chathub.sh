#!/bin/bash
# Chat hub (docs/plans/2026-10-03-chat-hub-design.md): Tuwunel plus one mautrix bridge
# per network, on the hub machine only. Role-specific, so it runs only when invoked
# explicitly on that machine; root install.sh does not call it. Re-running converges
# every bridge config on the values below and keeps each bridge's tokens.
# It also registers the chat relay of dojo's Compose file (registrations/envoy-relay.yaml);
# its url is null, so Tuwunel sends it nothing, until envoy-relay.on exists in the state
# folder. This call writes only that file and, when it changed, the marker that makes the
# next full run restart Tuwunel; it runs neither mise nor systemctl:
#   env -u XDG_DATA_HOME ~/.dotfiles/installers/chathub.sh relay-registration
# Switch the relay on, or off:
#   touch ~/.local/share/chathub/envoy-relay.on && env -u XDG_DATA_HOME ~/.dotfiles/installers/chathub.sh
#   rm -f ~/.local/share/chathub/envoy-relay.on && env -u XDG_DATA_HOME ~/.dotfiles/installers/chathub.sh
set -euo pipefail
# shellcheck source=installers/lib.sh
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
MISE="${DOTFILES_DIR}/bin/mise"
STATE="${XDG_DATA_HOME:-$HOME/.local/share}/chathub"
RELAY_REG="$STATE/registrations/envoy-relay.yaml"
RELAY_SWITCH="$STATE/envoy-relay.on"
RELAY_URL="http://127.0.0.1:29342"
RESTART_PENDING="$STATE/tuwunel-restart-pending"

# The chat relay's registration. Its user namespace is Sami alone (anchored: Tuwunel matches
# a namespace unanchored), so Tuwunel sends it each event of his rooms. Tokens are read back
# as plain text, without mise, and a missing or unusable one is made new; both are checked
# before anything is written, since Tuwunel stops loading registrations/ at a file it cannot
# parse. The file is replaced only when its text changes. The marker that asks for a restart
# is made before the file is replaced, so a run that stops partway never loses the restart.
write_relay_registration() {
    local as_token="" hs_token="" url=null tmp
    local token_re='^[0-9a-f]{64}$'
    install -d -m 0700 "$STATE" "$STATE/registrations"
    if [ -f "$RELAY_REG" ]; then
        as_token="$(sed -n 's/^as_token: \([0-9a-f]\{64\}\)$/\1/p' "$RELAY_REG")"
        hs_token="$(sed -n 's/^hs_token: \([0-9a-f]\{64\}\)$/\1/p' "$RELAY_REG")"
    fi
    [[ $as_token =~ $token_re ]] || as_token="$(openssl rand -hex 32)"
    [[ $hs_token =~ $token_re && $hs_token != "$as_token" ]] || hs_token="$(openssl rand -hex 32)"
    if ! [[ $as_token =~ $token_re && $hs_token =~ $token_re && $as_token != "$hs_token" ]]; then
        echo "ERROR: could not make two tokens for $RELAY_REG; nothing written." >&2
        exit 1
    fi
    [ -f "$RELAY_SWITCH" ] && url="'$RELAY_URL'"
    tmp="$(mktemp "$STATE/.envoy-relay.XXXXXX")" # mktemp makes it 0600
    printf '%s\n' \
        "id: envoy-relay" \
        "url: $url" \
        "as_token: $as_token" \
        "hs_token: $hs_token" \
        "sender_localpart: envoy-relay" \
        "rate_limited: false" \
        "namespaces:" \
        "  users:" \
        "    - regex: '^@sami:chathub\$'" \
        "      exclusive: false" >"$tmp"
    if cmp -s "$tmp" "$RELAY_REG"; then
        rm -f "$tmp"
        return
    fi
    touch "$RESTART_PENDING" || { rm -f "$tmp"; exit 1; }
    mv -f "$tmp" "$RELAY_REG"
}

case "$#:${1:-}" in
    0:) ;;
    1:relay-registration) write_relay_registration; exit 0 ;;
    *) echo "usage: $0 [relay-registration]" >&2; exit 2 ;;
esac

# instance  appservice-port  bot  config-layout
# The instance name is the binary's suffix: chathub/mautrix@.service runs mautrix-%i.
BRIDGES=(
    "gmessages 29336 gmessagesbot bridgev2"
    "whatsapp 29318 whatsappbot bridgev2"
    "signal 29328 signalbot bridgev2"
    "meta 29319 metabot bridgev2"
    "instagram 29320 instagrambot bridgev2"
    "discord 29334 discordbot legacy"
    "linkedin 29341 linkedinbot bridgev2"
)

for tool in github:matrix-construct/tuwunel github:mautrix/gmessages github:mautrix/whatsapp \
    github:mautrix/signal github:mautrix/discord github:mautrix/meta mautrix-instagram \
    github:mautrix/linkedin github:mautrix/manager; do
    "$MISE" install "$tool" >/dev/null
done
# chathub/login-screen runs mautrix-manager on Xvfb (it ships with the desktop) and shows it
# through Chromium's DevTools over `forward port`; it needs no other package.
YQ="$("$MISE" which yq)"

install -d -m 0700 "$STATE"
install -d -m 0700 "$STATE/registrations"

changed=()

# Double puppeting: one registration whose user namespace covers the hub, so every
# bridge can act as @sami, joining new chat rooms for him and posting what he sends
# from his phone as him. It has no url, so Tuwunel sends it no events. The restart marker is
# made before the file, so a run that stops partway never loses the restart.
dp_reg="$STATE/registrations/doublepuppet.yaml"
if [ ! -f "$dp_reg" ]; then
    touch "$RESTART_PENDING"
    (
        umask 077
        cat >"$dp_reg" <<EOF
id: doublepuppet
url: null
as_token: $(openssl rand -hex 32)
hs_token: $(openssl rand -hex 32)
sender_localpart: $(openssl rand -hex 16)
rate_limited: false
namespaces:
  users:
    - regex: '@.*:chathub'
      exclusive: false
EOF
    )
fi
dp_secret="as_token:$("$YQ" '.as_token' "$dp_reg")"

write_relay_registration

# Render one bridge's config from its own example, then pin every value the hub
# depends on. Assignments, not a merge: a merge would keep the example's
# `"*": relay` permission. Tokens come from the registration step and survive reruns.
render_bridge() {
    local name=$1 port=$2 bot=$3 layout=$4
    local dir="$STATE/$name" cfg="$STATE/$name/config.yaml" reg="$STATE/registrations/$name.yaml"
    local db="file:$dir/$name.db?_txlock=immediate"
    install -d -m 0700 "$dir"
    local before=""
    [ -f "$cfg" ] && before="$(sha256sum "$cfg")"
    if [ ! -f "$cfg" ] && [ "$layout" = bridgev2 ]; then
        "$MISE" exec -- "mautrix-$name" -e -c "$cfg" >/dev/null
    elif [ ! -f "$cfg" ]; then
        # Legacy bridges have no -e; their example config ships in the repo at the
        # release tag the binary was pinned from.
        local version
        version="$("$MISE" current "github:mautrix/$name")"
        curl -fsSL "https://raw.githubusercontent.com/mautrix/$name/v$version/example-config.yaml" -o "$cfg"
    fi

    local common=".homeserver.address = \"http://127.0.0.1:8008\"
        | .homeserver.domain = \"chathub\"
        | .appservice.address = \"http://127.0.0.1:$port\"
        | .appservice.hostname = \"127.0.0.1\"
        | .appservice.port = $port
        | .appservice.id = \"$name\"
        | .appservice.bot.username = \"$bot\"
        | .bridge.permissions = {\"@sami:chathub\": \"admin\"}"
    if [ "$layout" = bridgev2 ]; then
        "$YQ" -i "$common
            | .database.type = \"sqlite3-fk-wal\"
            | .database.uri = \"$db\"
            | .matrix.message_status_events = true
            | .double_puppet.secrets = {\"chathub\": \"$dp_secret\"}
            | .encryption.allow = false
            | .provisioning.shared_secret = \"generate\"
            | .provisioning.allow_matrix_auth = true
            | .backfill.enabled = true
            | .backfill.max_initial_messages = 50" "$cfg"
    else
        "$YQ" -i "$common
            | .appservice.database.type = \"sqlite3-fk-wal\"
            | .appservice.database.uri = \"$db\"
            | .bridge.message_status_events = true
            | .bridge.login_shared_secret_map = {\"chathub\": \"$dp_secret\"}
            | .bridge.backfill.forward_limits.initial.dm = 50
            | .bridge.backfill.forward_limits.missed.dm = -1
            | .bridge.encryption.allow = false" "$cfg"
    fi
    # One user, so name people the way Sami's phone does. The bridges leave contact-list
    # names out by default only so they don't leak between users of a shared bridge.
    local template=""
    case $name in
        whatsapp) template='{{or .FullName .BusinessName .PushName .Phone .RedactedPhone "Unknown user"}} (WA)' ;;
        signal) template='{{or .Nickname .ContactName .ProfileName .PhoneNumber "Unknown user"}}' ;;
    esac
    if [ -n "$template" ]; then
        TEMPLATE="$template" "$YQ" -i '.network.displayname_template = strenv(TEMPLATE)' "$cfg"
    fi
    chmod 600 "$cfg"
    # A running bridge read its config at startup; one this run corrected needs a restart.
    if [ -n "$before" ] && [ "$before" != "$(sha256sum "$cfg")" ]; then
        changed+=("$name")
    fi

    # The restart marker comes before the registration, so a run that stops partway never
    # loses the restart.
    if [ ! -f "$reg" ]; then
        touch "$RESTART_PENDING"
        "$MISE" exec -- "mautrix-$name" -g -c "$cfg" -r "$reg" >/dev/null
    fi
}

for bridge in "${BRIDGES[@]}"; do
    # shellcheck disable=SC2086 # each entry is four space-separated fields
    render_bridge $bridge
done

mkdir -p "${HOME}/.config/systemd/user"
for unit in tuwunel.service mautrix@.service; do
    ln -sfn "${DOTFILES_DIR}/chathub/${unit}" "${HOME}/.config/systemd/user/${unit}"
done

systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload the chat hub units (no user systemd session here?) — reload them on the hub machine."
systemctl --user enable --now tuwunel 2>/dev/null \
    || echo "NOTE: could not enable tuwunel (no user systemd session here?) — enable it on the hub machine."
# Tuwunel reads appservice_dir only at startup, so a new or changed registration needs one
# restart; the marker keeps that need across runs until a restart succeeds.
if [ -f "$RESTART_PENDING" ]; then
    if systemctl --user restart tuwunel 2>/dev/null; then
        rm -f "$RESTART_PENDING"
    else
        echo "NOTE: could not restart tuwunel after a registration changed — run this installer again on the hub machine."
    fi
fi
for bridge in "${BRIDGES[@]}"; do
    name="${bridge%% *}"
    # reenable, not enable: it moves an instance's wants-link when [Install] changes.
    { systemctl --user reenable "mautrix@$name" && systemctl --user start "mautrix@$name"; } 2>/dev/null \
        || echo "NOTE: could not enable mautrix@$name (no user systemd session here?) — enable it on the hub machine."
done
for name in "${changed[@]}"; do
    systemctl --user try-restart "mautrix@$name" 2>/dev/null \
        || echo "NOTE: could not restart mautrix@$name after its config changed — restart it on the hub machine."
done

echo "Chat hub installed. First run only: ${DOTFILES_DIR}/chathub/bootstrap-user creates @sami:chathub and the agent token."
