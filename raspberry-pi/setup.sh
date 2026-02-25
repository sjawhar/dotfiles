#!/bin/bash
# Raspberry Pi-specific system setup
# Run with sudo: sudo ./raspberry-pi/setup.sh
# Idempotent — safe to re-run.
set -euo pipefail

LOCALE="${1:-en_US.UTF-8}"

# ------------------------------------------------------------------------------
# Locale generation and system default
# ------------------------------------------------------------------------------

# Ensure the locale is listed in /etc/locale.gen (uncommented)
locale_entry="${LOCALE} $(echo "$LOCALE" | sed 's/.*\.//')"
if ! grep -qx "$locale_entry" /etc/locale.gen 2>/dev/null; then
    sed -i "s/^# *${locale_entry}$/${locale_entry}/" /etc/locale.gen
    echo "Enabled ${LOCALE} in /etc/locale.gen"
fi

# Generate locales if ours is missing from the archive
if ! locale -a 2>/dev/null | grep -qi "$(echo "$LOCALE" | tr '.' ' ' | awk '{print $1}')"; then
    locale-gen
    echo "Generated locales"
fi

# Set system default
update-locale "LANG=${LOCALE}"
echo "System locale: ${LOCALE}"

# Ensure /etc/environment has LANG (read by pam_env.so)
if grep -q '^LANG=' /etc/environment 2>/dev/null; then
    sed -i "s/^LANG=.*/LANG=${LOCALE}/" /etc/environment
else
    echo "LANG=${LOCALE}" >> /etc/environment
fi

# ------------------------------------------------------------------------------
# PAM: fix pam_env.so readenv default (PAM >= 1.6 defaults to readenv=0)
#
# Without readenv=1, pam_env.so ignores envfile= directives entirely.
# The su PAM config ships with readenv=1, but sshd's does not — so SSH
# sessions don't pick up /etc/default/locale or /etc/environment.
# ------------------------------------------------------------------------------

fix_pam_readenv() {
    local pam_file="$1"
    [ -f "$pam_file" ] || return 0

    local changed=false

    # Add readenv=1 to pam_env.so lines that don't already have it
    if grep -qE 'pam_env\.so' "$pam_file" && grep -E 'pam_env\.so' "$pam_file" | grep -qv 'readenv='; then
        sed -i -E 's/(pam_env\.so)(([[:space:]]|$))/\1 readenv=1\2/' "$pam_file"
        changed=true
    fi

    if [ "$changed" = true ]; then
        echo "Fixed readenv=1 in ${pam_file}"
    else
        echo "readenv=1 already set in ${pam_file}"
    fi
}

fix_pam_readenv /etc/pam.d/sshd

echo "Done. Reconnect SSH to apply."
