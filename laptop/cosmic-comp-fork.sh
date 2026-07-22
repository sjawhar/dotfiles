#!/bin/bash
set -euo pipefail

# Forked cosmic-comp: adds WorkspaceOnOutput / MoveToWorkspaceOnOutput /
# SendToWorkspaceOnOutput shortcut actions (AeroSpace-style global workspaces
# addressed by output name or EDID model), and makes ext-workspace-v1 Activate
# transfer keyboard focus + pointer cross-output.
#
# Forks:       https://github.com/sjawhar/cosmic-comp  (switch-to-output)
#              https://github.com/sjawhar/cosmic-settings-daemon  (output-addressed-actions)
# Upstream:    https://github.com/pop-os/cosmic-comp/issues/2120  (PR links TBD)
# Stock binary backed up at /usr/bin/cosmic-comp.stock; rollback from a TTY:
#   sudo install -m 755 /usr/bin/cosmic-comp.stock /usr/bin/cosmic-comp
#
# Once the PRs merge and ship in Pop:
#   sudo apt-mark unhold cosmic-comp && sudo apt-get upgrade cosmic-comp
#   then delete this installer and its line in laptop/install.sh.

# Package held so Pop updates don't clobber the patched binary.
if ! apt-mark showhold 2>/dev/null | grep -qx cosmic-comp; then
    echo "Holding cosmic-comp package (patched fork installed)..."
    sudo apt-mark hold cosmic-comp
fi

# dpkg -V reports a checksum mismatch on /usr/bin/cosmic-comp when the fork
# build is installed. No mismatch = stock binary = the fork needs (re)installing.
if ! dpkg -V cosmic-comp 2>/dev/null | grep -q "/usr/bin/cosmic-comp$"; then
    echo "NOTE: /usr/bin/cosmic-comp is the stock build, not the patched fork."
    echo "  git clone -b switch-to-output https://github.com/sjawhar/cosmic-comp"
    echo "  cd cosmic-comp && mise x rust@1.93.0 -- cargo build --release"
    echo "  sudo test -e /usr/bin/cosmic-comp.stock || sudo install -m 755 /usr/bin/cosmic-comp /usr/bin/cosmic-comp.stock"
    echo "  sudo install -m 755 target/release/cosmic-comp /usr/bin/cosmic-comp"
fi
