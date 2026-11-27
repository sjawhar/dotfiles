#!/bin/bash
set -euo pipefail

# Locally built kernel.org kernel (laptop/build-kernel), carried until Pop ships a
# kernel with AMD's fix e7ba638b8116 "drm/amd/display: Avoid closing unowned DDC
# pins" (CVE-2026-99628, first in 7.3.5). Pop's 7.2 kernels lack it: unplugging a
# Thunderbolt dock or monitor can oops amdgpu and freeze the display until a hard reset.
#
# Rollback: choose the previous kernel in the boot menu (hold Space at power-on), or
#   sudo apt-get remove linux-image-<release> linux-headers-<release>
#   sudo linux-update-symlinks remove <release> /boot/vmlinuz-<release>
#   sudo kernelstub --verbose --preserve-live-mode
# where <release> is the output of laptop/build-kernel --release.
#
# Once Pop ships 7.3.5 or later: remove the local kernel as above, then delete
# laptop/build-kernel, this file and its line in laptop/install.sh.

local_kernel=$("${LAPTOP_DIR}/build-kernel" --release)
pop_kernel=$(dpkg-query -W -f='${Version}' linux-system76 2>/dev/null || true)

if [[ -n $pop_kernel ]] && dpkg --compare-versions "$pop_kernel" ge 7.3.5; then
    if dpkg -s "linux-image-$local_kernel" &>/dev/null; then
        echo "NOTE: Pop's kernel $pop_kernel has AMD's DDC fix; remove the local kernel $local_kernel (see laptop/local-kernel.sh)."
    fi
elif ! dpkg -s "linux-image-$local_kernel" &>/dev/null; then
    echo "NOTE: Pop's kernel lacks AMD's DDC fix and the local kernel $local_kernel is not installed; undocking can freeze the display."
    echo "  ${LAPTOP_DIR}/build-kernel && ${LAPTOP_DIR}/build-kernel --install, then reboot"
elif [[ $(uname -r) != "$local_kernel" ]]; then
    echo "NOTE: the local kernel $local_kernel is installed but $(uname -r) is running; reboot into $local_kernel."
fi
