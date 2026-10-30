#!/bin/bash
# Devbox-specific setup (remote dev machine). Not sourced from the main install.sh.
set -euo pipefail
# shellcheck source=devbox/../installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEVBOX_DIR="${DOTFILES_DIR}/devbox"

# --- YubiKey PC/SC channel: owned by forward (forward serve serves
# ~/.pcscd/pcscd.comm and relays to the laptop's forward daemon). The old
# SSH-tunnel socat bridge is retired on this machine; clean it up if present.
# (oryx still uses the tunnel pattern — see devbox/pcscd-bridge.service.)
mkdir -p ~/.pcscd ~/.config/systemd/user
if systemctl --user is-enabled pcscd-bridge.service &>/dev/null; then
    systemctl --user disable --now pcscd-bridge.service || true
fi
rm -f ~/.config/systemd/user/pcscd-bridge.service
systemctl --user daemon-reload

# Devbox serve role exposes files through the laptop tunnel without binding the laptop's forwarded port.
bash "${DOTFILES_DIR}/installers/forward.sh" serve

# One embedding server for every omp session on the box (see installers/omp-embed.sh):
# without it each session loads its own ~1 GB copy of the mnemopi embedding model.
bash "${DOTFILES_DIR}/installers/omp-embed.sh"

# The secrets broker's host helper (AGENTC-393): agent sessions run here, so this machine
# registers them and enrolls their boxes (see installers/agent-secrets.sh).
bash "${DOTFILES_DIR}/installers/agent-secrets.sh"

# On an Azure VM enrolled in box-identity, gcloud and ADC act as dev-sjawhar with no
# sign-in; Sami's login stays in configuration `theorem` (see installers/gcloud-box.sh).
bash "${DOTFILES_DIR}/installers/gcloud-box.sh"

# Model-spend records from every harness, now that gcloud acts as the box identity, which reads the collector's token,
# with the host name as theorem.lane (see installers/agent-spend.sh).
AGENT_SPEND_LANE="$(hostname -s)" bash "${DOTFILES_DIR}/installers/agent-spend.sh"

# Swap, reclaim settings, a ceiling on this user's slice and the session shed daemon, so one
# runaway session can no longer wedge the box (see installers/omp-box-memory-guard.sh).
bash "${DOTFILES_DIR}/installers/omp-box-memory-guard.sh"

# The empty tmp* directories Python's mkdtemp leaves in TMPDIR on the big disk, pruned daily
# (devbox/tmp-empty-prune.service).
arm_user_timer devbox tmp-empty-prune

# The box's one Go build cache, /mnt/nvme/<user>/gocache (GOCACHE in .bashrc), cleaned hourly
# once it passes 80 GB (devbox/gocache-cap.service).
arm_user_timer devbox gocache-cap

# Each agent session's own TMPDIR under /mnt/nvme/<user>/session-tmp (scripts/agent-session),
# removed once no live process holds it (devbox/session-scratch-reap.md).
arm_user_timer devbox session-scratch-reap

# The shared uv, cargo and Go module caches on /mnt/nvme/<user>, each held under its own cap
# (devbox/nvme-cache-cap.md).
arm_user_timer devbox nvme-cache-cap

# Scratch on the big disk for the box's systemd --user services, which read environment.d and
# not .bashrc (devbox/nvme-tmp.conf). Installed only where /mnt/nvme/<user> exists and is
# writable, and only once both directories are there, since environment.d has no conditionals.
# A running unit keeps the environment it started with; each picks this up at its next start.
nvme_home="/mnt/nvme/$(id -un)"
if [ -d "$nvme_home" ] && mkdir -p "$nvme_home/tmp" "$nvme_home/gotmp" 2>/dev/null \
    && [ -w "$nvme_home/tmp" ] && [ -w "$nvme_home/gotmp" ]; then
    mkdir -p ~/.config/environment.d
    ensure_link "${DEVBOX_DIR}/nvme-tmp.conf" ~/.config/environment.d/nvme-tmp.conf
    if command -v systemctl &>/dev/null && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        systemctl --user set-environment TMPDIR="$nvme_home/tmp" GOTMPDIR="$nvme_home/gotmp" \
            || echo "WARNING: no reachable systemd --user manager; TMPDIR applies at next login" >&2
    fi
else
    echo "No writable $nvme_home: leaving the user manager's TMPDIR alone"
fi

echo "--- Devbox setup complete ---"
