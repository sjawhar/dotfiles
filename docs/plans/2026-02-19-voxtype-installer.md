# Voxtype Installer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a repeatable, fully idempotent dotfiles installer for voxtype (voice-to-text) with Groq cloud backend, ydotool output, and tray indicator.

**Architecture:** Single `installers/voxtype.sh` script following the existing pattern (source lib.sh, use ensure_link). Source files live in `voxtype/` dir in repo root. Daemon wrapper uses `mise exec` to avoid hardcoded version paths.

**Tech Stack:** Bash (installer), Python 3 + AyatanaAppIndicator3 (tray), systemd user services, sops + age (secrets)

---

### Task 1: Move existing files into `voxtype/` directory

Move the files we created during the session into the dotfiles repo under `voxtype/`.

**Files:**
- Create: `voxtype/config.toml` (copy from `~/.config/voxtype/config.toml`)
- Create: `voxtype/voxtype-daemon` (copy from `~/.local/bin/voxtype-daemon`, then modify)
- Create: `voxtype/voxtype-tray` (copy from `~/.local/bin/voxtype-tray`)
- Create: `voxtype/voxtype-tray.desktop` (copy from `~/.config/autostart/voxtype-tray.desktop`, then modify)
- Create: `voxtype/groq.conf` (copy from `~/.config/systemd/user/voxtype.service.d/groq.conf`)

**Step 1: Copy files into voxtype/ directory**

```bash
mkdir -p ~/.dotfiles/voxtype
cp ~/.config/voxtype/config.toml ~/.dotfiles/voxtype/config.toml
cp ~/.local/bin/voxtype-daemon ~/.dotfiles/voxtype/voxtype-daemon
cp ~/.local/bin/voxtype-tray ~/.dotfiles/voxtype/voxtype-tray
cp ~/.config/autostart/voxtype-tray.desktop ~/.dotfiles/voxtype/voxtype-tray.desktop
cp ~/.config/systemd/user/voxtype.service.d/groq.conf ~/.dotfiles/voxtype/groq.conf
```

**Step 2: Update voxtype-daemon to use `mise exec` instead of hardcoded paths**

Replace the entire daemon wrapper with:

```bash
#!/bin/bash
# Wrapper to launch voxtype daemon with Groq API key from sops secrets
set -euo pipefail

MISE="${DOTFILES_DIR:-$HOME/.dotfiles}/bin/mise"

export VOXTYPE_WHISPER_API_KEY
VOXTYPE_WHISPER_API_KEY=$("$MISE" exec sops age -- \
    sops -d --output-type dotenv "$HOME/.dotfiles/secrets.env" \
    | grep '^GROQ_API_KEY=' | cut -d= -f2-)

exec /usr/lib/voxtype/voxtype-avx512 daemon
```

Key change: `mise exec sops age -- sops ...` instead of hardcoded `$HOME/.mise/installs/sops/3.11.0/sops`. The `mise exec` command resolves the current pinned versions from `mise.toml` at runtime, so version bumps don't break the wrapper.

**Step 3: Ensure scripts are executable**

```bash
chmod +x ~/.dotfiles/voxtype/voxtype-daemon ~/.dotfiles/voxtype/voxtype-tray
```

**Step 4: Commit**

```bash
cd ~/.dotfiles
git add voxtype/
git commit -m "Add voxtype config and scripts to dotfiles repo"
```

---

### Task 2: Create `installers/voxtype.sh`

**Files:**
- Create: `installers/voxtype.sh`

**Step 1: Write the installer script**

```bash
#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VOXTYPE_VERSION="0.6.2"
VOXTYPE_ARCH="amd64"  # only x86_64 supported for now

# --- .deb package ---
installed_version=$(dpkg-query -W -f='${Version}' voxtype 2>/dev/null || echo "")
if [ "$installed_version" != "${VOXTYPE_VERSION}-1" ]; then
    echo "Installing voxtype ${VOXTYPE_VERSION}..."
    deb_url="https://github.com/peteonrails/voxtype/releases/download/v${VOXTYPE_VERSION}/voxtype_${VOXTYPE_VERSION}-1_${VOXTYPE_ARCH}.deb"
    tmp_deb="$(mktemp --suffix=.deb)"
    curl -fSL -o "$tmp_deb" "$deb_url"
    sudo dpkg -i "$tmp_deb" || sudo apt-get install -f -y
    rm -f "$tmp_deb"
fi

# --- apt dependencies ---
sudo apt-get install -y -qq ydotool gir1.2-ayatanaappindicator3-0.1 >/dev/null

# --- udev rule for /dev/uinput access (ydotool) ---
UDEV_RULE='KERNEL=="uinput", GROUP="input", MODE="0660"'
UDEV_FILE="/etc/udev/rules.d/99-uinput.rules"
if ! grep -qxF "$UDEV_RULE" "$UDEV_FILE" 2>/dev/null; then
    echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger /dev/uinput 2>/dev/null || true
fi

# --- input group ---
if ! id -nG "$USER" | grep -qw input; then
    sudo usermod -aG input "$USER"
    echo "NOTE: Added $USER to input group. Reboot required for systemd to pick it up."
fi

# --- Symlinks ---
mkdir -p ~/.config/voxtype
mkdir -p ~/.local/bin
mkdir -p ~/.config/autostart
mkdir -p ~/.config/systemd/user/voxtype.service.d

ensure_link "${DOTFILES_DIR}/voxtype/config.toml"            ~/.config/voxtype/config.toml
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-daemon"          ~/.local/bin/voxtype-daemon
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-tray"            ~/.local/bin/voxtype-tray
ensure_link "${DOTFILES_DIR}/voxtype/voxtype-tray.desktop"    ~/.config/autostart/voxtype-tray.desktop
ensure_link "${DOTFILES_DIR}/voxtype/groq.conf"               ~/.config/systemd/user/voxtype.service.d/groq.conf

# --- Services ---
sudo systemctl enable --now ydotool 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now voxtype 2>/dev/null || true

# --- Manual step reminders ---
if [ ! -f ~/.config/sops/age/keys.txt ]; then
    echo "WARNING: ~/.config/sops/age/keys.txt not found."
    echo "  The voxtype daemon needs this to decrypt the Groq API key."
    echo "  Write your age private key to this file."
fi

echo "NOTE: Configure COSMIC keyboard shortcut manually:"
echo "  Settings > Keyboard > Custom Shortcuts"
echo "  Name: Voxtype Toggle"
echo "  Command: voxtype record toggle"
echo "  Shortcut: Super+V (or your preference)"
```

**Step 2: Commit**

```bash
cd ~/.dotfiles
git add installers/voxtype.sh
git commit -m "Add voxtype installer script"
```

---

### Task 3: Wire into `install.sh` and verify

**Files:**
- Modify: `install.sh` (add source line after opencode.sh)

**Step 1: Add voxtype installer to install.sh**

Add after the `source "${DOTFILES_DIR}/installers/opencode.sh"` line:

```bash
source "${DOTFILES_DIR}/installers/voxtype.sh"
```

**Step 2: Run the installer in isolation to verify**

```bash
cd ~/.dotfiles && source installers/voxtype.sh
```

Expected: all symlinks created, services running, no errors. The deb install and apt steps should be no-ops since everything is already installed.

**Step 3: Verify idempotency — run it again**

```bash
cd ~/.dotfiles && source installers/voxtype.sh
```

Expected: same output, no errors, no changes.

**Step 4: Verify voxtype still works**

```bash
systemctl --user status voxtype
voxtype record toggle  # start recording
# speak briefly
voxtype record toggle  # stop and transcribe
```

**Step 5: Commit**

```bash
cd ~/.dotfiles
git add install.sh
git commit -m "Wire voxtype installer into install.sh"
```
