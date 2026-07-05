#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

# =============================================================================
# joycon - Nintendo Joy-Cons for Amazon Luna in the browser (see joycon/README.md)
# =============================================================================
echo "--- joycon (Joy-Con -> Luna remap + rumble) ---"

JOYCON_DIR="${DOTFILES_DIR}/laptop/joycon"
BIN=/usr/local/bin/joycon-remap
SRC="${JOYCON_DIR}/joycon-remap.c"

# --- joycond (upstream; not packaged for Ubuntu, so build + install from source) ---
# Pinned to a known-good commit so ~/Code/joycond is not needed and a fresh
# machine reproduces the exact binary. joycond's `make install` also lays down
# its udev rules, systemd service, and the hid-nintendo module-load conf.
JOYCOND_REF="0df025ac5dc284b1f31172b6b252321ba788c4de"
if [ ! -x /usr/bin/joycond ]; then
    echo "Building joycond (${JOYCOND_REF:0:9}) from source..."
    for dep in cmake libudev-dev libevdev-dev; do
        dpkg -s "$dep" &>/dev/null || sudo apt-get install -y -qq "$dep" >/dev/null
    done
    tmp="$(mktemp -d)"
    git clone https://github.com/DanielOgorchock/joycond "$tmp/joycond"
    ( cd "$tmp/joycond" && git checkout -q "$JOYCOND_REF" \
        && cmake . >/dev/null && make -j"$(nproc)" && sudo make install )
    rm -rf "$tmp"
    sudo udevadm control --reload-rules
    sudo systemctl daemon-reload
fi

# --- build the remapper (single C file, libc only; no cargo/clone/deps) ---
if [ ! -x "$BIN" ] || [ "$SRC" -nt "$BIN" ]; then
    echo "Building joycon-remap..."
    tmp="$(mktemp)"
    cc -O2 -Wall -o "$tmp" "$SRC"
    sudo install -m0755 "$tmp" "$BIN"
    rm -f "$tmp"
fi

# --- udev rule (uaccess for output + stable symlink for combined device) ---
RULE_DEST="/etc/udev/rules.d/95-joycon-remap.rules"
if ! diff -q "${JOYCON_DIR}/95-joycon-remap.rules" "$RULE_DEST" &>/dev/null; then
    echo "Installing joycon udev rule..."
    sudo cp "${JOYCON_DIR}/95-joycon-remap.rules" "$RULE_DEST"
    sudo udevadm control --reload-rules
fi

# --- systemd units (system-level: joycon-remap needs /dev/uinput as root) ---
units_changed=false
for u in joycon-remap.service joycon-remap.path; do
    if ! diff -q "${JOYCON_DIR}/$u" "/etc/systemd/system/$u" &>/dev/null; then
        echo "Installing $u..."
        sudo cp "${JOYCON_DIR}/$u" "/etc/systemd/system/$u"
        units_changed=true
    fi
done
if [ "$units_changed" = true ]; then
    sudo systemctl daemon-reload
fi

# --- joycond: on-demand only, no autostart (started by 'joycon on') ---
if [ "$(systemctl is-enabled joycond 2>/dev/null || true)" = "enabled" ]; then
    echo "Disabling joycond autostart (on-demand via 'joycon on')..."
    sudo systemctl disable joycond
fi

echo "joycon ready. Use: joycon {on|off|status}"
