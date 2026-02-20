# Voxtype Installer Design

## Summary

Add `installers/voxtype.sh` to the dotfiles repo, following the existing installer pattern. Consolidates the manual setup of voxtype (push-to-talk voice transcription using Groq cloud) into a repeatable, idempotent script.

## Context

Voxtype is installed via .deb from GitHub releases. It runs as a systemd user service with a custom daemon wrapper that decrypts the Groq API key from sops-encrypted secrets. Output uses ydotool (kernel uinput) because wtype is broken on COSMIC (cosmic-comp#2081). A Python tray indicator shows recording state.

## Files managed

All source files live in `voxtype/` in the dotfiles repo root:

| Source (dotfiles)            | Target                                                    | Method       |
|------------------------------|-----------------------------------------------------------|--------------|
| `voxtype/config.toml`        | `~/.config/voxtype/config.toml`                           | `ensure_link` |
| `voxtype/voxtype-daemon`     | `~/.local/bin/voxtype-daemon`                             | `ensure_link` |
| `voxtype/voxtype-tray`       | `~/.local/bin/voxtype-tray`                               | `ensure_link` |
| `voxtype/voxtype-tray.desktop` | `~/.config/autostart/voxtype-tray.desktop`              | `ensure_link` |
| `voxtype/groq.conf`          | `~/.config/systemd/user/voxtype.service.d/groq.conf`     | `ensure_link` |

## Install steps

All steps are idempotent (safe to re-run).

1. **Download and install .deb** — Pinned version variable (`VOXTYPE_VERSION`). Skip if already installed at that version (`dpkg -s voxtype` + version check). Download via `curl` from GitHub releases, install with `sudo dpkg -i`.

2. **apt dependencies** — `sudo apt install -y ydotool gir1.2-ayatanaappindicator3-0.1` (apt handles idempotency).

3. **udev rule** — Write `/etc/udev/rules.d/99-uinput.rules` via `sudo tee` only if content differs. Reload udev rules.

4. **input group** — `sudo usermod -aG input "$USER"` (idempotent — no-op if already member).

5. **Symlinks** — `ensure_link` for all 5 files, creating parent directories as needed.

6. **Enable services** — `systemctl --user daemon-reload`, `systemctl --user enable --now voxtype`, `sudo systemctl enable --now ydotool`.

7. **Print manual steps** — Remind about COSMIC keyboard shortcut (Super+V -> `voxtype record toggle`). Warn if `~/.config/sops/age/keys.txt` doesn't exist.

## Daemon wrapper changes

Replace hardcoded mise install paths with `mise exec` using the real mise binary:

```bash
MISE="${DOTFILES_DIR:-$HOME/.dotfiles}/bin/mise"
VOXTYPE_WHISPER_API_KEY=$("$MISE" exec sops age -- sops -d --output-type dotenv "$HOME/.dotfiles/secrets.env" \
    | grep '^GROQ_API_KEY=' | cut -d= -f2-)
```

This eliminates fragile version-pinned paths.

## Out of scope

- Whisper model downloads (Groq mode = no local models)
- COSMIC keyboard shortcut configuration (manual)
- Creating `~/.config/sops/age/keys.txt` (manual, one-time)
- Adding GROQ_API_KEY to secrets.env (already present)

## Integration

Add `source "${DOTFILES_DIR}/installers/voxtype.sh"` to `install.sh` after the `opencode.sh` line.
