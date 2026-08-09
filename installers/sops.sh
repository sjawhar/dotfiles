#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# SOPS_AGE_KEY must be forwarded via SSH SendEnv from the local machine. Without
# it we skip entirely: sops can neither bootstrap nor decrypt secrets.env. An
# already-committed .sops.yaml is never overwritten (see the guard below) — it
# holds the full tiered recipient roster and regenerating it would be destructive.

if [ -z "${SOPS_AGE_KEY:-}" ]; then
    echo "SOPS_AGE_KEY not set — skipping sops/age setup."
    echo "  Forward it from your local machine: ssh -o SendEnv=SOPS_AGE_KEY ..."
    return 0 2>/dev/null || exit 0
fi

# Bootstrap .sops.yaml ONLY when absent. Never clobber an existing file: the
# committed .sops.yaml carries the full tiered roster (human tier + agent tier),
# and regenerating it with this one machine key would wipe the human gate and
# make every tier decryptable unattended. Idempotent, like the other installers.
if [ -f "${DOTFILES_DIR}/.sops.yaml" ]; then
    echo ".sops.yaml already exists — leaving the committed recipient roster untouched."
else
    echo "Bootstrapping .sops.yaml with this machine's age recipient..."
    AGE_PUBLIC_KEY="$(echo "$SOPS_AGE_KEY" | age-keygen -y)"
    cat > "${DOTFILES_DIR}/.sops.yaml" <<EOF
creation_rules:
  - age: '${AGE_PUBLIC_KEY}'
EOF
fi

# Create initial secrets.env if it doesn't exist
if [ ! -f "${DOTFILES_DIR}/secrets.env" ]; then
    echo "Creating initial encrypted secrets.env..."
    tmp="$(mktemp)"
    echo "# Secrets managed by sops + age" > "$tmp"
    sops -e --input-type dotenv --output-type dotenv "$tmp" > "${DOTFILES_DIR}/secrets.env"
    rm "$tmp"
fi

echo "sops/age secrets management configured."
