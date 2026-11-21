#!/bin/bash
# Devbox-specific setup (remote dev machine). Not sourced from the main install.sh.
set -euo pipefail
# shellcheck source=devbox/../installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEVBOX_DIR="${DOTFILES_DIR}/devbox"

# Generate this VM's literal tailnet serve config and transfer the PC/SC socket
# from the legacy SSH bridge to forward, preserving the bridge's unit file.
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

# gcloud and ADC act as the machine's own identity with no sign-in: the account the metadata
# server serves on a Google VM, the box-identity account on an enrolled Azure VM (see
# installers/gcloud-box.sh).
bash "${DOTFILES_DIR}/installers/gcloud-box.sh"

# Outside Google, serve the box's cached token to keyless Delta readers on loopback.
bash "${DOTFILES_DIR}/installers/gcloud-metadata.sh"

# On a Google VM, the box-identity paths scripts from sami-rocqperf name (/mnt/nvme/<user>/boxid/
# gcloud-box and infra-kubeconfig), as the machine's account (see installers/gcloud-boxid.sh).
bash "${DOTFILES_DIR}/installers/gcloud-boxid.sh"

# On a Google VM, the host's metadata relay and the variables that point every host client at it,
# when ~/.config/agent-identity/account names an account other than the VM's own; with no such file
# it changes nothing. After gcloud-box.sh and gcloud-boxid.sh, which it runs again through the
# relay (see installers/agent-identity.sh).
bash "${DOTFILES_DIR}/installers/agent-identity.sh"

# On a machine with no person's GitHub login (the Google VM, sami-devbox), git outside an agent
# session gets the GitHub App's token too, and ssh remotes of github.com go over https (see
# installers/gh-app-host.sh).
bash "${DOTFILES_DIR}/installers/gh-app-host.sh"

# On a Google VM, the grading farm's CLI as sami-rocqperf has it: `farm` on PATH, its venv, its
# config and the box identity's ssh key in OS Login. After gcloud-boxid.sh (the key goes in as
# that identity) and gh-app-host.sh (it may clone grading-tools) (see installers/grade-farm.sh).
bash "${DOTFILES_DIR}/installers/grade-farm.sh"

# Model-spend records from every harness, now that gcloud acts as the box identity, which reads the collector's token,
# with the host name as theorem.lane (see installers/agent-spend.sh).
AGENT_SPEND_LANE="$(hostname -s)" bash "${DOTFILES_DIR}/installers/agent-spend.sh"

# Swap, reclaim settings, a ceiling on this user's slice and one on each login session scope,
# so neither the sessions together nor a single unbounded login can wedge the box (see
# installers/omp-box-memory-guard.sh).
bash "${DOTFILES_DIR}/installers/omp-box-memory-guard.sh"

# The empty tmp* directories Python's mkdtemp leaves in TMPDIR on the big disk, pruned daily
# (devbox/tmp-empty-prune.service).
arm_user_timer devbox tmp-empty-prune

# The box's one Go build cache, <scratch>/gocache (GOCACHE in .bashrc; <scratch> is
# /mnt/scratch/<user> where devbox/gce-disks makes it, else /mnt/nvme/<user>), trimmed past 25 GB
# without removing an entry a running build may read (devbox/gocache-cap.service).
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

# dotfiles-follow is NOT armed on this box. Sami's test for a timer on his dev box
# (dispatch://INFRA-2022/ask/b5ddab54-8ba0-40e8-b4ba-c2dcb291ca5c): a unit living in these dotfiles
# is not authorization for it, and "we don't need custom timer units"; on two timers that watched
# nothing (dispatch://INFRA-2022/ask/4badbed6-a78d-4405-adcb-cbe2679e12d3): "Both go ... somewhere
# that is not a timer on my box." Nothing of his approves this one. Here ~/.dotfiles moves onto
# main when scripts/dotfiles-publish runs from it, or by `scripts/dotfiles-sync local`. oryx arms
# the follower on oryx (oryx/install.sh). A Google VM that armed it before keeps running it from
# its links, so they come off here: the timer is disabled and stopped (a run under way ends by
# itself), and each link is removed only when it points at this repository's unit.
if "${DOTFILES_DIR}/scripts/on-google-vm"; then
    follow_links="$HOME/.config/systemd/user"
    follow_removed=0
    if [[ -L "$follow_links/dotfiles-follow.timer" \
        && "$(readlink -- "$follow_links/dotfiles-follow.timer")" == "$HOME/.dotfiles/devbox/dotfiles-follow.timer" ]]; then
        echo "dotfiles-follow: timer was $(systemctl --user is-enabled dotfiles-follow.timer 2>&1 || true)/$(systemctl --user is-active dotfiles-follow.timer 2>&1 || true); disabling it"
        systemctl --user disable --now dotfiles-follow.timer
    fi
    for suffix in timer service; do
        if [[ -L "$follow_links/dotfiles-follow.$suffix" \
            && "$(readlink -- "$follow_links/dotfiles-follow.$suffix")" == "$HOME/.dotfiles/devbox/dotfiles-follow.$suffix" ]]; then
            rm -- "$follow_links/dotfiles-follow.$suffix"
            echo "dotfiles-follow: removed $follow_links/dotfiles-follow.$suffix"
            follow_removed=1
        fi
    done
    if (( follow_removed )); then
        systemctl --user daemon-reload
    fi
fi

# transcript-lag-watch is NOT armed here, and both halves of that are deliberate.
#
# It contradicted itself: this installer armed it on every box while devbox/expected-timers.txt
# recorded it as taken out on 2026-11-05 because sami-devbox has no write path to the transcript
# store. A machine cannot both expect a timer and expect it gone (dispatch://INFRA-2079 row M9).
#
# The contradiction is resolved by removing it rather than by expecting it, on Sami's answer at
# dispatch://INFRA-2022/ask/b5ddab54-8ba0-40e8-b4ba-c2dcb291ca5c: a dev box is monitored by the
# standard Datadog or Cloud agent, "we don't need custom timer units for it", and a unit living in
# these dotfiles is not authorization for it. It was stopped on sami-rocqperf at 2026-11-09
# 00:27Z; what it watched — the store's newest entry from a host going stale — is moot there,
# since hosts.tsv gives that host no row of its own and sami-devbox imports its lines under
# held_by.

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

# On a Google VM, the key agent boxes use to reach this host's own sshd (a box's connection to the
# host's tailnet address reaches OpenSSH, not Tailscale SSH), made here, accepted only from the
# agentbox network, and the host's pinned key (see installers/agentbox-host-ssh.sh).
if "${DOTFILES_DIR}/scripts/on-google-vm"; then
    bash "${DOTFILES_DIR}/installers/agentbox-host-ssh.sh"
fi

echo "--- Devbox setup complete ---"
