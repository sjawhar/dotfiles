#!/bin/bash
# Chat hub (docs/plans/2026-10-03-chat-hub-design.md): Tuwunel plus one mautrix bridge
# per network, on the hub machine only. Role-specific, so it runs only when invoked
# explicitly on that machine; root install.sh does not call it. Re-running converges
# every bridge config on the values below and keeps each bridge's tokens.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
MISE="${DOTFILES_DIR}/bin/mise"
STATE="${XDG_DATA_HOME:-$HOME/.local/share}/chathub"

# instance  appservice-port  bot  config-layout
# The instance name is the binary's suffix: chathub/mautrix@.service runs mautrix-%i.
BRIDGES=(
    "gmessages 29336 gmessagesbot bridgev2"
    "whatsapp 29318 whatsappbot bridgev2"
    "signal 29328 signalbot bridgev2"
    "meta 29319 metabot bridgev2"
    "instagram 29320 instagrambot bridgev2"
    "discord 29334 discordbot legacy"
)

for tool in github:matrix-construct/tuwunel github:mautrix/gmessages github:mautrix/whatsapp \
    github:mautrix/signal github:mautrix/discord github:mautrix/meta mautrix-instagram; do
    "$MISE" install "$tool" >/dev/null
done
YQ="$("$MISE" which yq)"

install -d -m 0700 "$STATE"
install -d -m 0700 "$STATE/registrations"

new_registration=false
changed=()

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
            | .encryption.allow = false
            | .provisioning.shared_secret = \"disable\"
            | .backfill.enabled = true
            | .backfill.max_initial_messages = 50" "$cfg"
    else
        "$YQ" -i "$common
            | .appservice.database.type = \"sqlite3-fk-wal\"
            | .appservice.database.uri = \"$db\"
            | .bridge.encryption.allow = false" "$cfg"
    fi
    chmod 600 "$cfg"
    # A running bridge read its config at startup; one this run corrected needs a restart.
    if [ -n "$before" ] && [ "$before" != "$(sha256sum "$cfg")" ]; then
        changed+=("$name")
    fi

    if [ ! -f "$reg" ]; then
        "$MISE" exec -- "mautrix-$name" -g -c "$cfg" -r "$reg" >/dev/null
        new_registration=true
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
# Tuwunel reads appservice_dir at startup, so a new bridge registration needs a restart.
if [ "$new_registration" = true ]; then
    systemctl --user restart tuwunel 2>/dev/null \
        || echo "NOTE: could not restart tuwunel after a new registration — restart it on the hub machine."
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
