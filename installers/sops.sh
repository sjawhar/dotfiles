#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# SOPS_AGE_KEY must be forwarded via SSH SendEnv from local machine.
# Without it, we can still write .sops.yaml if it already exists, but can't
# create or decrypt secrets.env.

if [ -z "${SOPS_AGE_KEY:-}" ]; then
    echo "SOPS_AGE_KEY not set — skipping sops/age setup."
    echo "  Forward it from your local machine: ssh -o SendEnv=SOPS_AGE_KEY ..."
    return 0 2>/dev/null || exit 0
fi

# Derive public key from private key
AGE_PUBLIC_KEY="$(echo "$SOPS_AGE_KEY" | age-keygen -y)"

# Write .sops.yaml with the age public key as recipient
cat > "${DOTFILES_DIR}/.sops.yaml" <<EOF
creation_rules:
  - age: '${AGE_PUBLIC_KEY}'
EOF

# Create initial secrets.env if it doesn't exist
if [ ! -f "${DOTFILES_DIR}/secrets.env" ]; then
    echo "Creating initial encrypted secrets.env..."
    tmp="$(mktemp)"
    echo "# Secrets managed by sops + age" > "$tmp"
    sops -e --input-type dotenv --output-type dotenv "$tmp" > "${DOTFILES_DIR}/secrets.env"
    rm "$tmp"
fi

echo "sops/age secrets management configured."
