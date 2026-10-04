#!/bin/bash
set -euo pipefail

# Forked iced inside xdg-desktop-portal-cosmic: closing the window iced bound its
# clipboard to no longer drops the clipboard connection, so a screenshot copied to
# the clipboard survives the screenshot overlay closing (pop-os/cosmic-screenshot#212).
#
# Fork:      https://github.com/sjawhar/iced  (clipboard-survives-window-close)
# Upstream:  not submitted; pop-os does not accept LLM-generated changes
#            (https://github.com/pop-os/xdg-desktop-portal-cosmic/pull/353)
# Build:     laptop/build-xdg-desktop-portal-cosmic, then restart the portal.
# Rollback:  rm ~/.config/systemd/user/org.freedesktop.impl.portal.desktop.cosmic.service.d/local-build.conf
#            systemctl --user daemon-reload
#            systemctl --user restart org.freedesktop.impl.portal.desktop.cosmic.service

portal_bin=~/.local/libexec/xdg-desktop-portal-cosmic
portal_dropin_dir=~/.config/systemd/user/org.freedesktop.impl.portal.desktop.cosmic.service.d

# The override is linked only once the patched binary exists: pointing the unit
# at a missing binary would leave the desktop without a portal.
if [[ -x $portal_bin ]]; then
    mkdir -p "$portal_dropin_dir"
    ensure_link "${LAPTOP_DIR}/xdg-desktop-portal-cosmic-local-build.conf" "$portal_dropin_dir/local-build.conf"
    systemctl --user daemon-reload 2>/dev/null || true
    if [[ "$(cat "$portal_bin.pkgver" 2>/dev/null)" != "$(dpkg-query -W -f='${Version}' xdg-desktop-portal-cosmic)" ]]; then
        echo "NOTE: xdg-desktop-portal-cosmic was updated, but the portal runs the patched build of an older version."
        echo "  ${LAPTOP_DIR}/build-xdg-desktop-portal-cosmic"
        echo "  systemctl --user restart org.freedesktop.impl.portal.desktop.cosmic.service"
    fi
else
    echo "NOTE: the patched xdg-desktop-portal-cosmic is not built; screenshots copied to the clipboard can paste nothing."
    echo "  ${LAPTOP_DIR}/build-xdg-desktop-portal-cosmic, then re-run laptop/install.sh"
fi
