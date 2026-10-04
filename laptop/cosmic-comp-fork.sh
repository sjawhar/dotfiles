#!/bin/bash
set -euo pipefail

# Forked cosmic-comp, built from branch `sami` of sjawhar/cosmic-comp: Pop's
# packaged cosmic-comp revision plus these branches (knives tracks them; run
# `knives status` / `knives notch` in the checkout):
#   output-workspaces     WorkspaceOnOutput / MoveToWorkspaceOnOutput /
#                         SendToWorkspaceOnOutput shortcut actions, which address a
#                         numbered workspace slot on an output named by connector or
#                         EDID model (Alt+3 is slot 3 whether or not slots 1-2 exist),
#                         and ext-workspace-v1 Activate transferring keyboard focus +
#                         pointer cross-output. The actions come from branch `sami` of
#                         sjawhar/cosmic-settings-daemon.
#   kms-surface-waits     5 s bound on the main thread's waits for KMS surface threads
#                         (DisplayLink dock freezes, pop-os/cosmic-comp#2693).
#   fix-active-num-panic  pop-os/cosmic-comp#2784 (panic when an output is removed).
# Pop does not accept AI-written PRs, so these live in the fork; drop a branch once
# Pop ships an equivalent.
# Stock binary backed up at /usr/bin/cosmic-comp.stock; rollback from a TTY:
#   sudo install -m 755 /usr/bin/cosmic-comp.stock /usr/bin/cosmic-comp
#
# Moving the fork to a newer Pop revision: install that package version first so
# its shipped defaults (shortcuts, window rules) match, refresh the .stock backup,
# then reinstall the fork binary and hold the package again.

# Package held so Pop updates don't clobber the patched binary.
if ! apt-mark showhold 2>/dev/null | grep -qx cosmic-comp; then
    echo "Holding cosmic-comp package (patched fork installed)..."
    sudo apt-mark hold cosmic-comp
fi

# dpkg -V reports a checksum mismatch on /usr/bin/cosmic-comp when the fork
# build is installed. No mismatch = stock binary = the fork needs (re)installing.
if ! dpkg -V cosmic-comp 2>/dev/null | grep -q "/usr/bin/cosmic-comp$"; then
    echo "NOTE: /usr/bin/cosmic-comp is the stock build, not the patched fork."
    echo "  git clone -b sami https://github.com/sjawhar/cosmic-comp"
    echo "  cd cosmic-comp && mise x rust@1.93.0 -- cargo build --release"
    echo "  sudo test -e /usr/bin/cosmic-comp.stock || sudo install -m 755 /usr/bin/cosmic-comp /usr/bin/cosmic-comp.stock"
    echo "  sudo install -m 755 target/release/cosmic-comp /usr/bin/cosmic-comp"
fi
