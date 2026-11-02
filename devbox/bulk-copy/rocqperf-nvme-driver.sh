#!/usr/bin/env bash
# sami-rocqperf /mnt/nvme/sami, then /home/sami (to the stage), in parallel with the /mnt/durable
# driver (dispatch://INFRA-993 step 3). Starts only after io some avg60 stayed under 50 for 10
# monitor samples (Main, 2026-11-02 18:44Z). Shard lists stream from the bucket (made on
# sami-devbox), so nothing is written on rocqperf's /mnt/nvme or /. Run in a unit with io.max on
# md127. PAR from $1 (default 8).
set -u
export DEVBOX_COPY_DIR=${DEVBOX_COPY_DIR:-/mnt/durable/sami/devbox-copy}
export DEVBOX_COPY_LISTS=${DEVBOX_COPY_LISTS:-gs://sami-devbox-copy-usw4/lists/sami-rocqperf}
cd "$DEVBOX_COPY_DIR" || exit 1
PAR=${1:-8}
# Calm: the last 10 monitor samples all have io some avg60 under 50 and gate=OK (no floor breach).
calm() { tail -10 monitor-sami-rocqperf.log | awk '{for(i=1;i<=NF;i++) if($i ~ /^io_some60=/){split($i,a,"="); if(a[2]+0 >= 50) bad=1; n++} if($0 !~ /gate=OK/ || $0 ~ /lfloor=1/) bad=1} END{exit !(n==10 && !bad)}'; }
no_incident() { [ "$(jq -r '.incidents | length' /mnt/nvme/sami/tmp/disk-fill-watch/state.json 2>/dev/null)" = 0 ]; }
until calm && no_incident; do sleep 60; done
echo "nvme driver start $(date -u +%FT%TZ)" >>driver.log
./upload.sh sami-rocqperf nvme-sami /mnt/nvme/sami "$PAR"
./upload.sh sami-rocqperf home /home/sami "$PAR"
echo "nvme driver done $(date -u +%FT%TZ)" >>driver.log
