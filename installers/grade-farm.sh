#!/bin/bash
set -euo pipefail

# The grading farm's CLI (`farm`, theorem-labs/grading-tools grade-farm/) on a Google VM
# (sami-devbox, dispatch://INFRA-993), installed as sami-rocqperf has it:
#   /mnt/nvme/<user>/farm-pkg/src        a grading-tools clone (cloned here when absent)
#   /mnt/nvme/<user>/farm-pkg/venv       uv venv, CPython 3.13, grade-farm installed from src (not
#                                        editable; it is stdlib only, so the lock pins nothing)
#   /mnt/nvme/<user>/farm-pkg/bin/farm   the shim that runs it with the farm root as FARM_DIR
#   ~/.local/bin/farm                    a link to the shim, so `farm` is on PATH on the host and
#                                        in every box (agentbox/mounts binds ~/.local/bin)
#   ~/.config/grade-farm/config          the host's FARM_* paths (README, "The host's own paths");
#                                        written only when absent; agentbox/mounts binds it ro
#   /mnt/nvme/<user>/boxid/sshkey        the box identity's ssh key. Made here, never copied: the
#                                        box identity (OS Login user sa_101805078591270564613, the
#                                        account of boxid/gcloud-box, installers/gcloud-boxid.sh)
#                                        accepts any key in its OS Login profile, so this host
#                                        adds its own public half there.
# The farm root (/mnt/nvme/<user>/grade-farm: fleet, boxes, ssh_config, known_hosts, the
# no-start switch) is state, not install: devbox/sami-devbox.md says where it comes from.
# A box runs `farm` with `--dir /mnt/nvme/<user>/farm-pkg --dir /mnt/nvme/<user>/grade-farm
# --dir /mnt/nvme/<user>/boxid`. Devbox role installer: devbox/install.sh runs it after
# gcloud-boxid.sh. Idempotent; prints each change. Elsewhere (not a Google VM, or no
# /mnt/nvme/<user>) it changes nothing.

here="$(dirname "${BASH_SOURCE[0]}")"
NVME="/mnt/nvme/$(id -un)"
PKG="${GRADE_FARM_PKG:-$NVME/farm-pkg}"
ROOT="${GRADE_FARM_ROOT:-$NVME/grade-farm}"
BOXID="${GRADE_FARM_BOXID:-$NVME/boxid}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/grade-farm/config"
PYTHON=3.13

if ! "$here/../scripts/on-google-vm"; then
    echo "grade-farm: not a Google VM; nothing changed."
    exit 0
fi
if [[ ! -d "$NVME" ]]; then
    echo "grade-farm: no $NVME; nothing changed."
    exit 0
fi

mkdir -p "$PKG/bin" "$ROOT"
if [[ ! -d "$PKG/src/.git" ]]; then
    git clone -q https://github.com/theorem-labs/grading-tools "$PKG/src"
    echo "grade-farm: cloned grading-tools into $PKG/src"
fi

installed="$PKG/venv/lib/python$PYTHON/site-packages/grade_farm"
if [[ "$("$PKG/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" != "$PYTHON" ]]; then
    rm -rf "$PKG/venv"
    uv venv -q --python "$PYTHON" "$PKG/venv"
    echo "grade-farm: made $PKG/venv (CPython $PYTHON)"
fi
if ! diff -rq --exclude=__pycache__ "$installed" "$PKG/src/grade-farm/src/grade_farm" >/dev/null 2>&1; then
    uv pip install -q --reinstall --python "$PKG/venv/bin/python" "$PKG/src/grade-farm"
    echo "grade-farm: installed grade-farm from $PKG/src into $PKG/venv"
fi

shim="$(mktemp)"
trap 'rm -f "$shim"' EXIT
cat >"$shim" <<EOF
#!/usr/bin/env bash
# The grade-farm package (theorem-labs/grading-tools grade-farm/), installed beside the live farm directory.
export FARM_DIR="\${FARM_DIR:-$ROOT}"
exec $PKG/venv/bin/farm "\$@"
EOF
if ! cmp -s "$shim" "$PKG/bin/farm"; then
    install -m 755 "$shim" "$PKG/bin/farm"
    echo "grade-farm: wrote $PKG/bin/farm"
fi
mkdir -p "$HOME/.local/bin"
if [[ "$(readlink "$HOME/.local/bin/farm" 2>/dev/null)" != "$PKG/bin/farm" ]]; then
    ln -sfn "$PKG/bin/farm" "$HOME/.local/bin/farm"
    echo "grade-farm: linked ~/.local/bin/farm to $PKG/bin/farm"
fi

if [[ ! -f "$CONFIG" ]]; then
    mkdir -p "$(dirname "$CONFIG")"
    # The BET clone's path comes from this machine's environment, never from this file: the repository is public.
    if [[ -n "${FARM_BET_REPO:-}" ]]; then
        bet_line="FARM_BET_REPO=$FARM_BET_REPO"
    else
        bet_line="# FARM_BET_REPO=<this host's BET clone>: unset, every farm images push is refused (grade-farm README)"
        echo "grade-farm: no FARM_BET_REPO in the environment; set it in $CONFIG before a farm images push"
    fi
    cat >"$CONFIG" <<EOF
# The grading farm host's own paths (grade-farm/README.md, "The host's own paths").
# Machine-local: this file is never in a repository. The environment wins over it.
# Written by ~/.dotfiles/installers/grade-farm.sh. No FARM_LEDGER_*: no COMPUTE.md row from here.
FARM_DIR=$ROOT
FARM_BOX_GCLOUD_CONFIG=$BOXID/gcloud-box
FARM_BOX_KEY=$BOXID/sshkey
FARM_MIRROR_ROOTS=/mnt/* $HOME
$bet_line
FARM_BLOBS_GCS=gs://tmp-transfer-autoformalization-16125/sami/grade-farm/blobs
FARM_IMAGES_GCS=gs://tmp-transfer-autoformalization-16125/sami/grade-farm
FARM_INVENTORY=$NVME/compute-inventory.csv
# This host is shared with interactive work: anything over 32 GB goes to the farm.
GRADE_SLOT_MAX_GB=32
GRADE_SLOT_BUDGET_HOSTS=*$(hostname -s)* *corpus-graph-grader* *proof-task-freeze* bet-grade-*
EOF
    echo "grade-farm: wrote $CONFIG"
fi

key="$BOXID/sshkey"
if [[ ! -f "$key" ]]; then
    ssh-keygen -q -t ed25519 -N "" -C "farm-box-identity@$(hostname -s)" -f "$key"
    echo "grade-farm: made $key"
fi
blob="$(awk '{print $2}' "$key.pub")"
if ! CLOUDSDK_CONFIG="$BOXID/gcloud-box" gcloud compute os-login describe-profile --format='value(sshPublicKeys)' | grep -qF "$blob"; then
    CLOUDSDK_CONFIG="$BOXID/gcloud-box" gcloud compute os-login ssh-keys add --key-file="$key.pub" >/dev/null
    echo "grade-farm: added $key.pub to the box identity's OS Login profile"
fi
echo "grade-farm: farm $PKG/bin/farm, root $ROOT, box key $key"
