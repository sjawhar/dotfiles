#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# agent-browser - the binary is mise-managed (`agent-browser` in mise.toml).
# It drives a Chrome for Testing build that it downloads itself into
# ~/.agent-browser/browsers/. `install` is idempotent: it skips a build that
# is already there. No --with-deps: that runs apt as root for Chrome's shared
# libraries, which these machines already have.
agent-browser install

# Ubuntu 23.10+ sets kernel.apparmor_restrict_unprivileged_userns=1, so a
# Chrome outside /opt/google/chrome cannot create the user namespace its
# sandbox needs and exits with "No usable sandbox!". Give the downloaded build
# the same unconfined-with-userns profile Ubuntu ships for /opt/google/chrome
# (/etc/apparmor.d/chrome), rather than run it with --no-sandbox. AppArmor
# profiles load into the host kernel, so the step runs on the host, not in an
# agent box.
if [[ -z "${AGENTBOX_BOX:-}" ]] &&
    [[ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null)" == 1 ]]; then
    profile=/etc/apparmor.d/agent-browser-chrome
    wanted="$(cat <<'EOF'
# Written by ~/.dotfiles/installers/agent-browser.sh. Lets the Chrome for
# Testing build agent-browser downloads create its sandbox's user namespace.
abi <abi/4.0>,
include <tunables/global>

profile agent-browser-chrome @{HOME}/.agent-browser/browsers/chrome-*/chrome flags=(unconfined) {
  userns,

  include if exists <local/agent-browser-chrome>
}
EOF
)"
    if [[ "$(cat "$profile" 2>/dev/null)" != "$wanted" ]]; then
        echo "agent-browser: installing AppArmor profile $profile"
        printf '%s\n' "$wanted" | sudo tee "$profile" >/dev/null
        sudo apparmor_parser -r "$profile"
    fi
fi
