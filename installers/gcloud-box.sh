#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Makes the machine's own identity the default Google identity of gcloud and of
# the application default credentials (ADC), on the two kinds of machine that
# have one. Role installer: devbox/install.sh runs it. Idempotent; says which
# branch ran and prints each change it makes.
#
# On a Google Compute Engine VM (scripts/on-google-vm; sami-devbox,
# dispatch://INFRA-993) the identity is the VM's attached service account, and
# every client takes its tokens from the metadata server with no file at all:
#   - gcloud: configuration `box`, whose account is the metadata server's
#     default account email and whose auth/credential_file_override and
#     auth/access_token_file are unset. gcloud checks those two properties and
#     then its own credential store before it asks the metadata server, for an
#     account the metadata server lists (lib/googlecloudsdk/core/credentials/
#     store.py, _Load and GceCredentialProvider), so a stored Azure
#     external_account entry (the Azure branch's, in a config dir carried over
#     from an Azure machine) is removed from credentials.db and access_tokens.db
#     by scripts/gcloud-drop-azure-credential; no other stored entry is touched.
#     shims/gcloud runs the same removal on any other config dir it is given.
#   - ADC: no box_credential_config.json and no well-known
#     application_default_credentials.json, so google-auth and every other
#     Google client library fall through to the metadata server. A well-known
#     file that is the Azure branch's external_account is removed; one that is
#     any other credential (a person's login) is reported and the run fails,
#     since every library on the machine would act as it.
#
# On an Azure VM enrolled in box-identity (theorem-labs/agent-infra
# box-identity/), gcloud's active configuration and the ADC become the service
# account dev-sjawhar, reached through workload identity federation from the
# VM's user-assigned managed identity box-sjawhar. No sign-in, nothing that
# expires. gcloud writes only the box account's own legacy_credentials entry.
# Besides that, the box configuration, active_config and gcloud's credential
# store, the one file changed is application_default_credentials.json; a user
# login found there is kept once as application_default_credentials.json.pre-box.
#
# On both, it creates no configuration for a person's login: an agent applies in
# CI or as agent-infra's applier, impersonated from this identity, and a step only
# a person can make is on agent-infra's docs/break-glass.md. Any other
# configuration that already exists, and its legacy_credentials, are never touched.
#
# On any other machine (not a Google VM, and no Azure IMDS identity that is
# box-sjawhar) it changes nothing.
#
# GCE_METADATA_HOST (the Google metadata server, as scripts/on-google-vm reads
# it) and GCLOUD_BOX_IMDS_URL (the Azure IMDS endpoint) override the endpoints
# for tests. CLOUDSDK_CONFIG is honoured, so a throwaway config dir can stand in
# for ~/.config/gcloud.

BOX_ACCOUNT="dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
BOX_PROJECT="autoformalization"
# Client ID of the managed identity box-sjawhar (resource group sami).
BOX_CLIENT_ID="15c6fb2c-a770-43c9-840b-291d65265bae"
WIF_PROVIDER="projects/805240912354/locations/global/workloadIdentityPools/azure/providers/entra"
APP_ID_URI="https://management.azure.com/"
IMDS_URL="${GCLOUD_BOX_IMDS_URL:-http://169.254.169.254}/metadata/identity/oauth2/token?api-version=2018-02-01&resource=${APP_ID_URI}"
GCE_METADATA="http://${GCE_METADATA_HOST:-metadata.google.internal}/computeMetadata/v1"

GCLOUD_DIR="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}"
CRED_FILE="${GCLOUD_DIR}/box_credential_config.json"
ADC_FILE="${GCLOUD_DIR}/application_default_credentials.json"

# The IMDS token's client_id names the managed identity; true only for box-sjawhar.
box_identity_present() {
    local client_id
    client_id="$(curl -fsS -m 2 --noproxy '*' -H 'Metadata: true' "$IMDS_URL" 2>/dev/null | jq -r '.client_id // empty' 2>/dev/null)" || return 1
    [[ "$client_id" == "$BOX_CLIENT_ID" ]]
}

if "$(dirname "${BASH_SOURCE[0]}")/../scripts/on-google-vm"; then
    branch=google
    # The account the VM runs as, which gcloud's GCE provider also takes as its default.
    BOX_ACCOUNT="$(curl -fsS -m 5 --noproxy '*' -H 'Metadata-Flavor: Google' \
        "${GCE_METADATA}/instance/service-accounts/default/email" 2>/dev/null)" || BOX_ACCOUNT=""
    [[ "$BOX_ACCOUNT" == *@*.gserviceaccount.com ]] || {
        echo "gcloud-box: Google VM, but its metadata server names no service account (got '${BOX_ACCOUNT}'); attach one to the VM" >&2
        exit 1
    }
    echo "gcloud-box: Google VM: gcloud and ADC use the attached account ${BOX_ACCOUNT} from the metadata server"
elif box_identity_present; then
    branch=azure
    echo "gcloud-box: Azure VM with the box-sjawhar managed identity: gcloud and ADC use ${BOX_ACCOUNT} through workload identity federation"
else
    echo "gcloud-box: not a Google VM, and no box-sjawhar managed identity at Azure IMDS; nothing changed."
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
        -u CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE -u CLOUDSDK_AUTH_ACCESS_TOKEN_FILE \
        CLOUDSDK_CONFIG="$GCLOUD_DIR" CLOUDSDK_CORE_DISABLE_PROMPTS=1 "$GCLOUD" "$@"
}

mkdir -p "$GCLOUD_DIR"

# Configuration `box` exists (never activated here; step "active" below does that).
ensure_box_configuration() {
    if [[ ! -f "${GCLOUD_DIR}/configurations/config_box" ]]; then
        gc config configurations create box --no-activate >/dev/null 2>&1
        echo "gcloud-box: created configuration box"
    fi
}

# Configuration `box`: account BOX_ACCOUNT, project BOX_PROJECT. The account is read from the
# configuration file itself: on a Google VM `config get core/account` answers the metadata
# server's default account when none is set (store.py, GceCredentialProvider.GetAccount), which
# would hide a configuration that names no account.
set_box_account_and_project() {
    if [[ "$(sed -n 's/^account[[:space:]]*=[[:space:]]*//p' "${GCLOUD_DIR}/configurations/config_box" 2>/dev/null)" != "$BOX_ACCOUNT" ]]; then
        gc --configuration box config set core/account "$BOX_ACCOUNT" >/dev/null 2>&1
        echo "gcloud-box: configuration box account -> ${BOX_ACCOUNT}"
    fi
    if [[ "$(gc --configuration box config get core/project 2>/dev/null)" != "$BOX_PROJECT" ]]; then
        gc --configuration box config set core/project "$BOX_PROJECT" >/dev/null 2>&1
        echo "gcloud-box: configuration box project -> ${BOX_PROJECT}"
    fi
}

if [[ "$branch" == google ]]; then
    # 1. No credential file: the metadata server is the credential.
    if [[ -e "$CRED_FILE" ]]; then
        rm -f "$CRED_FILE"
        echo "gcloud-box: removed ${CRED_FILE} (the Azure branch's credential; a Google VM needs none)"
    fi

    # 1b. A stored Azure external_account entry (a config dir carried over from an Azure box) would
    #     win over the metadata server; scripts/gcloud-drop-azure-credential removes it.
    "$(dirname "${BASH_SOURCE[0]}")/../scripts/gcloud-drop-azure-credential" "$GCLOUD_DIR" | sed 's/^/gcloud-box: /'

    # 2. Configuration `box`: the VM's account, the box project, no credential override.
    ensure_box_configuration
    set_box_account_and_project
    for property in auth/credential_file_override auth/access_token_file; do
        if [[ -n "$(gc --configuration box config get "$property" 2>/dev/null)" ]]; then
            gc --configuration box config unset "$property" >/dev/null 2>&1
            echo "gcloud-box: configuration box ${property} unset"
        fi
    done
else
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
    ensure_box_configuration
    stored="$(gc auth list --filter="account:${BOX_ACCOUNT}" --format='value(account)' 2>/dev/null)"
    if [[ "$cred_changed" == yes || "$stored" != "$BOX_ACCOUNT" ]]; then
        gc --configuration box auth login --cred-file="$CRED_FILE" >/dev/null 2>&1
        echo "gcloud-box: stored the ${BOX_ACCOUNT} credential in gcloud's credential store (from ${CRED_FILE})"
    fi
    set_box_account_and_project
fi

# 3. `box` is the active configuration.
active="$(cat "${GCLOUD_DIR}/active_config" 2>/dev/null || true)"
if [[ "$active" != box ]]; then
    gc config configurations activate box >/dev/null 2>&1
    echo "gcloud-box: active configuration ${active:-none} -> box"
fi

# 4. ADC.
if [[ "$branch" == google ]]; then
    # No well-known file, so ADC reaches the metadata server.
    if [[ -f "$ADC_FILE" ]]; then
        if [[ "$(jq -r '.type // empty' "$ADC_FILE" 2>/dev/null)" == external_account ]]; then
            rm -f "$ADC_FILE"
            echo "gcloud-box: removed ${ADC_FILE} (the Azure branch's external_account), so ADC is the metadata server"
        else
            echo "gcloud-box: ${ADC_FILE} holds a credential of type '$(jq -r '.type // "unreadable"' "$ADC_FILE" 2>/dev/null)': every Google library on this machine would act as it instead of ${BOX_ACCOUNT}. Not removing a login this did not write; remove it, then run this again." >&2
            exit 1
        fi
    fi
    echo "gcloud-box: gcloud and ADC act as ${BOX_ACCOUNT} from the metadata server; no credential file"
else
    # An external_account credential configuration is a valid ADC file.
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
fi

