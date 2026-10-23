#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Makes the box identity the default Google identity on an Azure VM enrolled in
# box-identity (theorem-labs/agent-infra box-identity/): gcloud's active
# configuration and the application default credentials (ADC) become the service
# account dev-sjawhar, reached through workload identity federation from the VM's
# user-assigned managed identity box-sjawhar. No sign-in, nothing that expires.
#
# Sami's own login stays in configuration `theorem` (admin work:
# `gcloud --configuration theorem`, or GCLOUD_ACCOUNT=theorem through
# shims/gcloud) and its ADC in legacy_credentials/<his account>/adc.json. Neither
# is touched here: gcloud writes only the box account's own legacy_credentials
# entry. Besides that, the box configuration, active_config and gcloud's credential
# store, the one file changed is application_default_credentials.json; a user
# login found there is kept once as application_default_credentials.json.pre-box.
#
# On any other machine (no Azure IMDS, or an IMDS identity that is not
# box-sjawhar) it changes nothing. Role installer: devbox/install.sh runs it.
# Idempotent; prints each change it makes.
#
# GCLOUD_BOX_IMDS_URL overrides the IMDS endpoint (tests). CLOUDSDK_CONFIG is
# honoured, so a throwaway config dir can stand in for ~/.config/gcloud.

BOX_ACCOUNT="dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
BOX_PROJECT="autoformalization"
# Client ID of the managed identity box-sjawhar (resource group sami).
BOX_CLIENT_ID="15c6fb2c-a770-43c9-840b-291d65265bae"
WIF_PROVIDER="projects/805240912354/locations/global/workloadIdentityPools/azure/providers/entra"
APP_ID_URI="https://management.azure.com/"
IMDS_URL="${GCLOUD_BOX_IMDS_URL:-http://169.254.169.254}/metadata/identity/oauth2/token?api-version=2018-02-01&resource=${APP_ID_URI}"

GCLOUD_DIR="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}"
CRED_FILE="${GCLOUD_DIR}/box_credential_config.json"
ADC_FILE="${GCLOUD_DIR}/application_default_credentials.json"

# The IMDS token's client_id names the managed identity; true only for box-sjawhar.
box_identity_present() {
    local client_id
    client_id="$(curl -fsS -m 2 --noproxy '*' -H 'Metadata: true' "$IMDS_URL" 2>/dev/null | jq -r '.client_id // empty' 2>/dev/null)" || return 1
    [[ "$client_id" == "$BOX_CLIENT_ID" ]]
}

if ! box_identity_present; then
    echo "gcloud-box: no box-sjawhar managed identity at Azure IMDS; nothing changed."
    exit 0
fi

# The real SDK binary, not shims/gcloud: the shim fails when no SDK is installed,
# and that failure would be hidden by the quiet calls below.
GCLOUD=""
shim="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")/../shims/gcloud")"
while IFS= read -r candidate; do
    [[ "$(readlink -f "$candidate")" == "$shim" ]] && continue
    GCLOUD="$candidate"
    break
done < <(type -ap gcloud)
[[ -n "$GCLOUD" ]] || GCLOUD="$HOME/.gcloud/google-cloud-sdk/bin/gcloud"
[[ -x "$GCLOUD" ]] || {
    echo "gcloud-box: box identity present but the Google Cloud SDK is not installed (looked on PATH and at $GCLOUD)" >&2
    exit 1
}

# Plain gcloud against GCLOUD_DIR: no shim account selection, no inherited
# configuration or credential override, no prompts.
gc() {
    env -u GCLOUD_ACCOUNT -u CLOUDSDK_ACTIVE_CONFIG_NAME -u GOOGLE_APPLICATION_CREDENTIALS \
        CLOUDSDK_CONFIG="$GCLOUD_DIR" CLOUDSDK_CORE_DISABLE_PROMPTS=1 "$GCLOUD" "$@"
}

mkdir -p "$GCLOUD_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# 1. The credential configuration (no secret: it tells Google's libraries to fetch
#    an IMDS token and exchange it). Generated, so no path or file is committed.
gc iam workload-identity-pools create-cred-config "$WIF_PROVIDER" \
    --service-account="$BOX_ACCOUNT" --azure --app-id-uri="$APP_ID_URI" \
    --output-file="$tmp" >/dev/null 2>&1
cred_changed=no
if ! cmp -s "$tmp" "$CRED_FILE"; then
    install -m 600 "$tmp" "$CRED_FILE"
    cred_changed=yes
    echo "gcloud-box: wrote ${CRED_FILE}"
fi

# 2. Configuration `box`: account dev-sjawhar, project autoformalization.
if [[ ! -f "${GCLOUD_DIR}/configurations/config_box" ]]; then
    gc config configurations create box --no-activate >/dev/null 2>&1
    echo "gcloud-box: created configuration box"
fi
stored="$(gc auth list --filter="account:${BOX_ACCOUNT}" --format='value(account)' 2>/dev/null)"
if [[ "$cred_changed" == yes || "$stored" != "$BOX_ACCOUNT" ]]; then
    gc --configuration box auth login --cred-file="$CRED_FILE" >/dev/null 2>&1
    echo "gcloud-box: stored the ${BOX_ACCOUNT} credential (gcloud auth login --cred-file)"
fi
if [[ "$(gc --configuration box config get core/account 2>/dev/null)" != "$BOX_ACCOUNT" ]]; then
    gc --configuration box config set core/account "$BOX_ACCOUNT" >/dev/null 2>&1
    echo "gcloud-box: configuration box account -> ${BOX_ACCOUNT}"
fi
if [[ "$(gc --configuration box config get core/project 2>/dev/null)" != "$BOX_PROJECT" ]]; then
    gc --configuration box config set core/project "$BOX_PROJECT" >/dev/null 2>&1
    echo "gcloud-box: configuration box project -> ${BOX_PROJECT}"
fi

# 3. `box` is the active configuration.
active="$(cat "${GCLOUD_DIR}/active_config" 2>/dev/null || true)"
if [[ "$active" != box ]]; then
    gc config configurations activate box >/dev/null 2>&1
    echo "gcloud-box: active configuration ${active:-none} -> box"
fi

# 4. ADC: an external_account credential configuration is a valid ADC file.
if ! cmp -s "$CRED_FILE" "$ADC_FILE"; then
    if [[ -f "$ADC_FILE" && ! -e "${ADC_FILE}.pre-box" ]] &&
        [[ "$(jq -r '.type // empty' "$ADC_FILE" 2>/dev/null)" != external_account ]]; then
        cp -p "$ADC_FILE" "${ADC_FILE}.pre-box"
        echo "gcloud-box: kept the previous ADC as ${ADC_FILE}.pre-box"
    fi
    install -m 600 "$CRED_FILE" "$ADC_FILE"
    echo "gcloud-box: ADC -> ${BOX_ACCOUNT} (${ADC_FILE})"
fi

echo "gcloud-box: gcloud and ADC act as ${BOX_ACCOUNT}"
if [[ -f "${GCLOUD_DIR}/configurations/config_theorem" ]]; then
    echo "gcloud-box: Sami's login is unchanged: gcloud --configuration theorem"
fi
