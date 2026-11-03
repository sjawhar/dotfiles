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

# The Google Cloud SDK where boxes can see it (~/.gcloud), with the GKE auth plugin and the
# dispatch cluster's context (see installers/gcloud-sdk.sh). Before gcloud-box.sh, which needs it.
bash "${DOTFILES_DIR}/installers/gcloud-sdk.sh"

# On an Azure VM enrolled in box-identity, gcloud and ADC act as dev-sjawhar with no
# sign-in; an apply impersonates agent-infra's applier from it (see installers/gcloud-box.sh).
bash "${DOTFILES_DIR}/installers/gcloud-box.sh"

# On a machine with no person's GitHub login (the Google VM, sami-devbox), git outside an agent
# session gets the GitHub App's token too, and ssh remotes of github.com go over https (see
# installers/gh-app-host.sh).
bash "${DOTFILES_DIR}/installers/gh-app-host.sh"

# Model-spend records from every harness, now that gcloud acts as the box identity, which reads the collector's token,
# with the host name as theorem.lane (see installers/agent-spend.sh).
AGENT_SPEND_LANE="$(hostname -s)" bash "${DOTFILES_DIR}/installers/agent-spend.sh"

# Swap, reclaim settings, a ceiling on this user's slice and the session shed daemon, so one
# runaway session can no longer wedge the box (see installers/omp-box-memory-guard.sh).
bash "${DOTFILES_DIR}/installers/omp-box-memory-guard.sh"

# The empty tmp* directories Python's mkdtemp leaves in TMPDIR on the big disk, pruned daily
# (devbox/tmp-empty-prune.service).
arm_user_timer devbox tmp-empty-prune

# The box's one Go build cache, <scratch>/gocache (GOCACHE in .bashrc; <scratch> is
# /mnt/scratch/<user> where devbox/gce-disks makes it, else /mnt/nvme/<user>), cleaned hourly
# once it passes 80 GB (devbox/gocache-cap.service).
arm_user_timer devbox gocache-cap

# Each agent session's own TMPDIR under <scratch>/session-tmp (scripts/agent-session),
# removed once no live process holds it (devbox/session-scratch-reap.md).
arm_user_timer devbox session-scratch-reap

# The working directories agents make by hand under /mnt/nvme/<user>/tmp, which nothing owns:
# report-only until a review of a week of its decisions passes (devbox/session-scratch-reap.md).
arm_user_timer devbox session-tmp-reap

# The /tmp/<name> paths tools and agents write by hand, which no TMPDIR redirect reaches and
# which systemd-tmpfiles' 30d rule never catches, removed once no process holds them and
# nothing in them has been written for 48 h (devbox/tmp-stale-reap.md).
arm_user_timer devbox tmp-stale-reap

# The shared uv, cargo and Go module caches on /mnt/nvme/<user>, each held under its own cap
# (devbox/nvme-cache-cap.md).
arm_user_timer devbox nvme-cache-cap

# Docker's build cache held under 50 GB, and untagged images and the anonymous volumes of removed
# containers removed, hourly, while Docker's disk is over 80% used (devbox/docker-cap.service).
arm_user_timer devbox docker-cap

# Every 5 min, tell the box-health owner over Envoy when a timer-driven user unit stops completing
# its runs, has them killed at its time limit, or its timer stops firing; the units come from the
# timers themselves (scripts/unit-health-watch.md). BOX_HEALTH_OWNER, when set, goes in its drop-in.
ensure_box_health_owner unit-health-watch
arm_user_timer devbox unit-health-watch

# On a Google VM (sami-devbox, dispatch://INFRA-993), every 10 min: fast-forward ~/.dotfiles to
# origin's main, then `mise install` and the readiness check's pin lines; tell the box-health owner
# over Envoy, once per condition, when it refuses (a change git sees, other than the SECRET_FILES
# copies of devbox/sami-devbox-provision; a main that is not a fast-forward) or a step fails
# (scripts/dotfiles-follow.md). Other machines are moved by scripts/dotfiles-sync.
if "${DOTFILES_DIR}/scripts/on-google-vm"; then
    ensure_box_health_owner dotfiles-follow
    arm_user_timer devbox dotfiles-follow
fi

# Every 10 min, tell the box-health owner over Envoy when the transcript store's newest entry from
# this host is over 45 min old on two checks in a row (scripts/transcript-lag-watch.md). Quiet on a
# machine with no transcript-import unit and no transcript-store config. BOX_HEALTH_OWNER, when
# set, goes in its drop-in.
ensure_box_health_owner transcript-lag-watch
arm_user_timer devbox transcript-lag-watch

# Scratch on the big disk for the box's systemd --user services, which read environment.d and
# not .bashrc (devbox/nvme-tmp.conf). Installed only where /mnt/nvme/<user> exists and is
# writable, and only once both directories are there, since environment.d has no conditionals.
# On sami-devbox, where devbox/gce-disks makes /mnt/scratch/<user> on the local SSDs, the scratch
# goes there instead (devbox/scratch-tmp.conf), and the other file's link is removed.
# A running unit keeps the environment it started with; each picks this up at its next start.
nvme_home="/mnt/nvme/$(id -un)"
tmp_home="$nvme_home" tmp_conf=nvme-tmp.conf other_conf=scratch-tmp.conf
if [ -d "/mnt/scratch/$(id -un)" ]; then
    tmp_home="/mnt/scratch/$(id -un)" tmp_conf=scratch-tmp.conf other_conf=nvme-tmp.conf
fi
if [ -d "$nvme_home" ] && mkdir -p "$tmp_home/tmp" "$tmp_home/gotmp" 2>/dev/null \
    && [ -w "$tmp_home/tmp" ] && [ -w "$tmp_home/gotmp" ]; then
    mkdir -p ~/.config/environment.d
    ensure_link "${DEVBOX_DIR}/${tmp_conf}" ~/.config/environment.d/${tmp_conf}
    if [ -L ~/.config/environment.d/${other_conf} ]; then rm -f ~/.config/environment.d/${other_conf}; fi
    if command -v systemctl &>/dev/null && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        systemctl --user set-environment TMPDIR="$tmp_home/tmp" GOTMPDIR="$tmp_home/gotmp" \
            || echo "WARNING: no reachable systemd --user manager; TMPDIR applies at next login" >&2
    fi
else
    echo "No writable $tmp_home: leaving the user manager's TMPDIR alone"
fi

# ~/.ssh/known_hosts append-only, so no session's program can write over the file every session
# shares; ssh's UpdateHostKeys is off for this user (see installers/ssh-known-hosts.sh).
bash "${DOTFILES_DIR}/installers/ssh-known-hosts.sh"

echo "--- Devbox setup complete ---"
