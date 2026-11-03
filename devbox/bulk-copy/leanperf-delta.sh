#!/usr/bin/env bash
# Delta pass from sami-leanperf to sami-devbox (dispatch://INFRA-993 step 3). Run on sami-leanperf.
# --update keeps every file that is newer on sami-devbox (PEER's lanes write there since its move);
# no --delete; no --inplace. The receiver runs as root under cgroup-guard (no ssh-scope mass write
# without it). Writes a log per tree in ~/devbox-copy/delta-<tree>-<date>.log.
# Usage: leanperf-delta.sh TREE...   (TREE: theorem-ext4 theorem-xfs theorem home)
set -u
cd ~/devbox-copy || exit 1
D=$(date -u +%Y%m%dT%H%MZ)
RP="sudo /mnt/nvme/sami/cutover/cgroup-guard.sh run -- rsync"   # keeps stdin since cb6c186
for t in "$@"; do
  case $t in
    home) src=/home/sami/ dst=/mnt/nvme/cutover-stage/sami-leanperf/home/sami/
          ex=(--exclude=/.cache --exclude=/.mise --exclude=/.elan --exclude=/devbox-copy --exclude=/.azure
              --exclude=/.config/gh/hosts.yml --exclude=application_default_credentials.json
              --exclude=legacy_credentials --exclude=credentialed_accounts.db --exclude=credentials.db
              --exclude=access_tokens.db --exclude=box_credential_config.json --exclude=azureProfile.json) ;;
    *) src=/$t/ dst=/mnt/nvme/$t/ ex=() ;;
  esac
  echo "start $t $(date -u +%FT%TZ)" >>"delta-$t-$D.log"
  sudo -n nice -n 19 ionice -c3 rsync -aHAX --numeric-ids --update --info=stats2 \
    -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -l sami" \
    --rsync-path="$RP" "${ex[@]}" "$src" "sami-devbox:$dst" >>"delta-$t-$D.log" 2>&1
  echo "end $t $(date -u +%FT%TZ) rc=$?" >>"delta-$t-$D.log"
done
