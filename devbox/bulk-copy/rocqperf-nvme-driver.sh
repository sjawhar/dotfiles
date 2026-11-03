#!/usr/bin/env bash
# sami-rocqperf /mnt/nvme/sami, then /home/sami (to the stage), second run with the approved scope
# cut (dispatch://INFRA-993 step 3, Main 2026-11-03). Each tree: a fresh listing (list-tree.sh,
# pruning the cut trees), filtered and sharded on sami-devbox (EXCLUDE + SCOPE files), shard lists
# streamed from the bucket, upload.sh. Tree names nvme-sami-2 and home-2: the 19 nvme-sami shards
# of the first run are already on sami-devbox. The monitor freezes this unit while the read wait of
# the disks in nv_devs is above 20 ms, and on any floor alert.
# Run in $DEVBOX_COPY_DIR on /mnt/durable; nothing is written on rocqperf's /mnt/nvme or /.
set -euo pipefail
export DEVBOX_COPY_DIR=${DEVBOX_COPY_DIR:-/mnt/durable/sami/devbox-copy}
cd "$DEVBOX_COPY_DIR"
PAR=${1:-8}
BKT=gs://sami-devbox-copy-usw4
DEV=sami@sami-devbox
log() { echo "$(date -u +%FT%TZ) $*" >>driver.log; }

tree() {  # NAME ROOT TARGET DEVS RULEFILE... -- MKSHARDS_ARG...
  local name=$1 root=$2 target=$3 devs=$4; shift 4
  local rules=() mk=() rb=() r
  while [[ $1 != -- ]]; do rules+=("$1"); shift; done; shift; mk=("$@")
  for r in "${rules[@]}"; do rb+=("$(basename "$r")"); done
  echo "$devs" >nv_devs
  if [[ ! -f lists/$name.find ]]; then
    log "$name: listing $root"
    sudo -n ./list-tree.sh "$root" "lists/$name.find" "${rules[@]}" 2>"lists/$name.find.err"
    sudo -n chown sami:sami "lists/$name.find"
  fi
  if ! gcloud storage ls "$BKT/lists/sami-rocqperf/$name/manifest.tsv" >/dev/null 2>&1; then
    log "$name: shard lists on sami-devbox"
    gcloud storage cp "lists/$name.find" "$BKT/listings/sami-rocqperf/$name.find" 2>/dev/null
    scp -q EXCLUDE "${rules[@]}" "$DEV:/mnt/nvme/sami/cutover/"
    ssh -o BatchMode=yes "$DEV" "set -e; cd /mnt/nvme/sami/cutover
      gcloud storage cp $BKT/listings/sami-rocqperf/$name.find lists/$name.find 2>/dev/null
      python3 exclude-filter.py $root EXCLUDE ${rb[*]} --records <lists/$name.find >lists/$name.filtered 2>lists/$name.excluded
      rm -rf shardlists/$name; python3 mkshards.py lists/$name.filtered $name --out shardlists ${mk[*]}
      gcloud storage cp -r shardlists/$name $BKT/lists/sami-rocqperf/ 2>/dev/null
      gcloud storage rm $BKT/listings/sami-rocqperf/$name.find 2>/dev/null
      grep -q '^sami-rocqperf $name ' trees-rocqperf.tsv || echo 'sami-rocqperf $name $target' >>trees-rocqperf.tsv" >>driver.log 2>&1
  fi
  log "$name: upload"
  DEVBOX_COPY_LISTS=$BKT/lists/sami-rocqperf ./upload.sh sami-rocqperf "$name" "$root" "$PAR"
  log "$name: uploaded"
}

tree nvme-sami-2 /mnt/nvme/sami /mnt/nvme/sami "nvme1n1 nvme2n1 nvme3n1 nvme4n1" \
  SCOPE-base-nvme-sami SCOPE-nvme-sami --
tree home-2 /home/sami /mnt/nvme/cutover-stage/sami-rocqperf/home/sami "nvme0n1" EXCLUDE -- \
  --exclude .config/gcloud/application_default_credentials.json --exclude .config/gcloud/legacy_credentials \
  --exclude .config/gcloud/credentialed_accounts.db --exclude .config/gcloud/credentials.db \
  --exclude .config/gcloud/access_tokens.db --exclude .azure --exclude .config/gh/hosts.yml \
  --exclude .cache --exclude .mise
log "nvme driver done"
