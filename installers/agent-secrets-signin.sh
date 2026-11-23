#!/bin/bash
set -euo pipefail

# The secrets broker's device sign-in client, for every process of this user on a Google VM
# (sami-devbox). `agent-secrets login` signs a person in with it, and the client renews that
# person's sign-in with it each time the broker ends the sign-in's enrollment, after its lease
# (about 15 minutes). A process finds it in ~/.config/agent-secrets/config, the client's own
# settings file, which a systemd unit, a tmux server started earlier and omp's eval kernel read as
# a login shell does (cybermonklabs/legion packages/envoy/cmd/cipher/config.go, clientConfigKeys);
# a variable that is set wins over the file. The two lines this sets there, keeping every other:
#   AGENT_SECRETS_SIGNIN_CLIENT_ID           the id of the Google OAuth client of type "TVs and
#                                            Limited Input devices" in theorem-agent-infra; a
#                                            client id is not a secret
#   AGENT_SECRETS_SIGNIN_CLIENT_SECRET_FILE  ~/.config/agent-secrets/signin-client-secret, mode
#                                            0600, which holds that client's secret
# The secret is version 1 of the Secret Manager secret broker-signin-device-client-secret, read as
# the account the metadata server serves: the read and the two values theorem-labs/agent-infra's
# box-identity/setup/first-boot.sh makes on a box it sets up (write_signin_secret, SIGNIN_CLIENT_ID).
# The value is never printed and never in a command's arguments: it goes from Secret Manager's
# answer, a file of this directory, to the secret file through base64 on a pipe.
# Devbox role installer: devbox/install.sh runs it after agent-secrets.sh. Idempotent; prints each
# change. Elsewhere (not a Google VM) it changes nothing. A refused read exits 1 naming the HTTP
# status, and changes no file. GCE_METADATA_HOST and SIGNIN_SECRET_MANAGER_URL name the endpoints
# for tests.

here="$(dirname "${BASH_SOURCE[0]}")"
SIGNIN_CLIENT_ID='805240912354-85d2j2rgpbcmucack62fincm9i3jq73i.apps.googleusercontent.com'
SIGNIN_SECRET_VERSION='projects/805240912354/secrets/broker-signin-device-client-secret/versions/1'
SECRET_MANAGER="${SIGNIN_SECRET_MANAGER_URL:-https://secretmanager.googleapis.com}"
METADATA="http://${GCE_METADATA_HOST:-metadata.google.internal}/computeMetadata/v1"
CONF_DIR="$HOME/.config/agent-secrets"
CONFIG="$CONF_DIR/config"
SECRET_FILE="$CONF_DIR/signin-client-secret"

if ! "$here/../scripts/on-google-vm"; then
    echo "agent-secrets-signin: not a Google VM; nothing changed."
    exit 0
fi
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"

answer="" secret="" config=""
trap 'rm -f "$answer" "$secret" "$config"' EXIT
answer="$(mktemp "$CONF_DIR/.signin-answer.XXXXXX")"
secret="$(mktemp "$CONF_DIR/.signin-client-secret.XXXXXX")"
config="$(mktemp "$CONF_DIR/.config.XXXXXX")"

# The bearer is read into a local before curl runs, and reaches curl on its stdin (-H @-), so it
# is in no command's arguments.
bearer="$(curl -fsS -m 10 --noproxy '*' -H 'Metadata-Flavor: Google' "$METADATA/instance/service-accounts/default/token" | jq -er .access_token)"
status="$(printf 'Authorization: Bearer %s' "$bearer" | curl -sS -m 30 --retry 3 -o "$answer" -w '%{http_code}' -H @- "$SECRET_MANAGER/v1/$SIGNIN_SECRET_VERSION:access")"
if [[ "$status" != 200 ]]; then
    echo "agent-secrets-signin: Secret Manager answered $status to the read of $SIGNIN_SECRET_VERSION as this machine's account; nothing changed" >&2
    exit 1
fi
jq -er .payload.data <"$answer" | base64 -d >"$secret"
if [[ ! -s "$secret" ]]; then
    echo "agent-secrets-signin: Secret Manager's answer for $SIGNIN_SECRET_VERSION held no value; nothing changed" >&2
    exit 1
fi
if ! cmp -s "$secret" "$SECRET_FILE"; then
    chmod 600 "$secret"
    mv -f "$secret" "$SECRET_FILE"
    echo "agent-secrets-signin: wrote the sign-in client's secret to $SECRET_FILE (0600)"
elif [[ "$(stat -c %a "$SECRET_FILE")" != 600 ]]; then
    chmod 600 "$SECRET_FILE"
    echo "agent-secrets-signin: set $SECRET_FILE to mode 0600"
fi

# Every line of the settings file but the two this sets, which the client reads as KEY=value with
# blanks allowed around the `=`, then those two.
{
    if [[ -f "$CONFIG" ]]; then
        grep -vE '^[[:space:]]*AGENT_SECRETS_SIGNIN_CLIENT_(ID|SECRET_FILE)[[:space:]]*=' "$CONFIG" || true
    fi
    printf 'AGENT_SECRETS_SIGNIN_CLIENT_ID=%s\n' "$SIGNIN_CLIENT_ID"
    printf 'AGENT_SECRETS_SIGNIN_CLIENT_SECRET_FILE=%s\n' "$SECRET_FILE"
} >"$config"
if ! cmp -s "$config" "$CONFIG"; then
    chmod 600 "$config"
    mv -f "$config" "$CONFIG"
    echo "agent-secrets-signin: wrote the sign-in client to $CONFIG"
elif [[ "$(stat -c %a "$CONFIG")" != 600 ]]; then
    chmod 600 "$CONFIG"
    echo "agent-secrets-signin: set $CONFIG to mode 0600"
fi
echo "agent-secrets-signin: $CONFIG names the sign-in client $SIGNIN_CLIENT_ID and its secret in $SECRET_FILE"
