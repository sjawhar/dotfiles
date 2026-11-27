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

# The secrets broker's device sign-in client in the client's own settings file, so every process
# of this user can renew a person's `agent-secrets login` once its enrollment ends (see
# installers/agent-secrets-signin.sh). On a Google VM only.
bash "${DOTFILES_DIR}/installers/agent-secrets-signin.sh"

# The Google Cloud SDK where boxes can see it (~/.gcloud), with the GKE auth plugin and the
# dispatch cluster's context (see installers/gcloud-sdk.sh). Before gcloud-box.sh, which needs it.
bash "${DOTFILES_DIR}/installers/gcloud-sdk.sh"

# theorem-session, agent-infra's command for the agent sessions on the dispatch cluster, from its
# pinned release at ~/.local/bin, checked by SHA-256 (see installers/theorem-session.sh). After
# agent-secrets.sh, whose broker mints the GitHub token the download needs. It reaches the cluster
# with Google's application default credentials (gcloud-box.sh, below), not a kubeconfig.
bash "${DOTFILES_DIR}/installers/theorem-session.sh"

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

# Model-spend records from every harness, now that gcloud acts as the box identity, which reads the collector's token,
# with the host name as theorem.lane (see installers/agent-spend.sh).
AGENT_SPEND_LANE="$(hostname -s)" bash "${DOTFILES_DIR}/installers/agent-spend.sh"

# Swap, reclaim settings, a ceiling on this user's slice and one on each login session scope,
# so neither the sessions together nor a single unbounded login can wedge the box (see
# installers/omp-box-memory-guard.sh).
bash "${DOTFILES_DIR}/installers/omp-box-memory-guard.sh"

# The box's seven disk cleaners run from one timer, box-cleanup.timer, every 15 minutes, each at
# its own minimum interval and with one log, and so does the leak check's hook sweep
# (devbox/box-cleanup.md says what each step does): tmp-empty-prune, tmp-stale-reap,
# session-scratch-reap, session-tmp-reap, gocache-cap, nvme-cache-cap and docker-cap. This
# installer used to arm each cleaner's own timer and never box-cleanup.timer, which sami-devbox had
# linked by hand on 2026-11-04, so a re-run would have run every cleaner twice. A box that still
# has one of those timers loses it here.
arm_user_timer devbox box-cleanup
for cleaner in tmp-empty-prune tmp-stale-reap session-scratch-reap session-tmp-reap gocache-cap \
    nvme-cache-cap docker-cap; do
    disarm_user_timer devbox "$cleaner"
done

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
    disarm_user_timer devbox dotfiles-follow
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

# GOCACHE and GOMODCACHE in Go's own env file, for the go commands that inherit neither from .bashrc
# or scripts/agent-session: systemd user units, and the tools omp's Python eval kernel runs (see
# installers/go-env.sh).
bash "${DOTFILES_DIR}/installers/go-env.sh"

# UV_CACHE_DIR, PIP_CACHE_DIR and BUN_INSTALL_CACHE_DIR in uv's, pip's and bun's own config
# files, for the same processes (see installers/cache-config.sh).
bash "${DOTFILES_DIR}/installers/cache-config.sh"

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
