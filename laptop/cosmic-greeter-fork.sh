#!/bin/bash
set -euo pipefail

# Forked cosmic-greeter: restarts the PAM conversation on user activity at the
# lock screen, so the YubiKey/fingerprint window is live when you sit down
# instead of having expired at lock time (stock behavior parks at the password
# prompt until a failed submission restarts the stack).
#
# Fork:        https://github.com/sjawhar/cosmic-greeter  (fix-restart-auth-on-activity)
# Upstream PR: https://github.com/pop-os/cosmic-greeter/pull/493  (fixes #99)
# Stock binary backed up at /usr/bin/cosmic-greeter.stock; rollback from a TTY:
#   sudo cp -a /usr/bin/cosmic-greeter.stock /usr/bin/cosmic-greeter
#
# Once the PR merges and ships in Pop:
#   sudo apt-mark unhold cosmic-greeter && sudo apt-get upgrade cosmic-greeter
#   then delete this installer and its line in laptop/install.sh.

# Package held so Pop updates don't clobber the patched binary.
if ! apt-mark showhold 2>/dev/null | grep -qx cosmic-greeter; then
    echo "Holding cosmic-greeter package (patched fork installed)..."
    sudo apt-mark hold cosmic-greeter
fi

# dpkg -V reports a checksum mismatch on /usr/bin/cosmic-greeter when the fork
# build is installed. No mismatch = stock binary = the fork needs (re)installing.
if ! dpkg -V cosmic-greeter 2>/dev/null | grep -q "/usr/bin/cosmic-greeter$"; then
    echo "NOTE: /usr/bin/cosmic-greeter is the stock build, not the patched fork."
    echo "  git clone -b fix-restart-auth-on-activity https://github.com/sjawhar/cosmic-greeter"
    echo "  cd cosmic-greeter && cargo build --release"
    echo "  sudo cp -a /usr/bin/cosmic-greeter /usr/bin/cosmic-greeter.stock"
    echo "  sudo cp target/release/cosmic-greeter /usr/bin/cosmic-greeter"
fi
