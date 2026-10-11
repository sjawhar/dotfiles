#!/bin/bash
set -euo pipefail

# Forked cosmic-greeter: restarts the PAM conversation on user activity at the
# lock screen, so the YubiKey/fingerprint window is live when you sit down
# instead of having expired at lock time (stock behavior parks at the password
# prompt until a failed submission restarts the stack).
#
# Fork:      https://github.com/sjawhar/cosmic-greeter  (restart-auth-conversation)
# Upstream:  https://github.com/pop-os/cosmic-greeter/issues/99. The open
#            https://github.com/pop-os/cosmic-greeter/pull/493 carries an earlier
#            version of this fix; Pop does not accept AI-written PRs, so the fork
#            carries it until Pop ships an equivalent.
# Stock binary backed up at /usr/bin/cosmic-greeter.stock; rollback from a TTY:
#   sudo install -m 755 /usr/bin/cosmic-greeter.stock /usr/bin/cosmic-greeter
#
# Moving the fork to a newer Pop revision: install that package version first,
# refresh the .stock backup, rebase the branch onto the revision the package was
# built from (the last field of its version), then reinstall the fork binary.
#
# Once Pop ships an equivalent:
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
    echo "  git clone -b restart-auth-conversation https://github.com/sjawhar/cosmic-greeter"
    echo "  cd cosmic-greeter && cargo build --release"
    echo "  sudo test -e /usr/bin/cosmic-greeter.stock || sudo install -m 755 /usr/bin/cosmic-greeter /usr/bin/cosmic-greeter.stock"
    echo "  sudo install -m 755 target/release/cosmic-greeter /usr/bin/cosmic-greeter"
fi
