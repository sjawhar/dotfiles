#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

echo "--- Driver quirks ---"

# =============================================================================
# MediaTek MT7925 WiFi+BT combo card
# =============================================================================
if lspci -n 2>/dev/null | grep -q '14c3:7925'; then
    echo "MediaTek MT7925 detected — applying driver quirks..."

    # --- WiFi power save: disable ---
    # Value 2 = disabled, 3 = enabled (default on Pop!_OS/Ubuntu)
    # Prevents WiFi disconnects on sleep with MT7925.
    POWERSAVE_CONF="/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf"
    if [ -f "$POWERSAVE_CONF" ] && grep -q 'wifi.powersave = 3' "$POWERSAVE_CONF" 2>/dev/null; then
        echo "  Disabling WiFi power save..."
        sudo sed -i 's/wifi.powersave = 3/wifi.powersave = 2/' "$POWERSAVE_CONF"
        sudo systemctl restart NetworkManager
    fi

    # --- Disable ASPM to fix Bluetooth ---
    # The MT7925 exposes Bluetooth as an internal USB device behind a hub.
    # With ASPM enabled, mt7925e initialization resets the chip and kills the
    # BT USB endpoint before btusb can download firmware (error -19 ENODEV).
    # Disabling ASPM on the mt7925e driver prevents this race condition.
    # Affects: ThinkPad T14/T16/X13 Gen 6, and other MT7925 laptops.
    if ! grep -q 'mt7925e.disable_aspm=1' /proc/cmdline 2>/dev/null; then
        if command -v kernelstub &>/dev/null; then
            echo "  Adding mt7925e.disable_aspm=1 kernel parameter (fixes Bluetooth)..."
            sudo kernelstub -a "mt7925e.disable_aspm=1"
            echo "  NOTE: Reboot required for Bluetooth fix to take effect."
        fi
    fi

    # --- USBGuard: ensure policy includes internal BT device ---
    # The MT7925 BT appears as an internal USB device (0e8d:e025) behind a
    # Genesys Logic hub (05e3:0610). If USBGuard policy was generated while BT
    # was broken (e.g., before ASPM fix or BIOS update), the policy will be
    # missing these devices and USBGuard will block them on every boot.
    # After any BIOS/firmware update, regenerate the policy:
    #   sudo usbguard generate-policy | sudo tee /etc/usbguard/rules.conf
    # Also remove via-port from the internal hub rule — the port number can
    # change across module reloads, but hash+parent-hash+connect-type is
    # sufficient for internal devices.
fi

# =============================================================================
# Chicony Integrated Camera (04f2:b840) — MJPEG format override
# =============================================================================
# Raw YUYV only supports 640x480. Higher resolutions require MJPEG.
# Without a udev rule, browsers default to YUYV and get stuck at 480p.
WEBCAM_RULES="/etc/udev/rules.d/99-webcam-format.rules"
if lsusb -d 04f2:b840 &>/dev/null; then
    if ! diff -q "${LAPTOP_DIR}/99-webcam-format.rules" "$WEBCAM_RULES" &>/dev/null; then
        echo "Installing webcam format udev rule..."
        sudo cp "${LAPTOP_DIR}/99-webcam-format.rules" "$WEBCAM_RULES"
        sudo udevadm control --reload-rules
    fi
fi
