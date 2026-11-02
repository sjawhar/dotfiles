#!/usr/bin/env bash
# sami-rocqperf upload order (dispatch://INFRA-993 step 3): /mnt/durable/sami first (its listing
# may still grow: a --partial shard set goes first, the rest after the listing ends), then the rest
# of /mnt/durable, then /mnt/nvme/sami, then the home (to the stage on sami-devbox).
# Run in /mnt/nvme/sami/scratch/devbox-copy, in a unit with io.max on nvme0n2. PAR from $1 (default 16).
set -u
cd /mnt/nvme/sami/scratch/devbox-copy || exit 1
PAR=${1:-16}
B=sami-rocqperf
LOGINS=(.config/gcloud/application_default_credentials.json .config/gcloud/legacy_credentials
  .config/gcloud/credentialed_accounts.db .config/gcloud/credentials.db .config/gcloud/access_tokens.db
  .azure .config/gh/hosts.yml .cache .mise)
mk() { nice -n 19 python3 mkshards.py "$@" >>driver.log 2>&1; }
wait_for() { while [ ! -f "$1" ]; do sleep 60; done; }

[ -f lists/durable-sami/manifest.tsv ] && ./upload.sh $B durable-sami /mnt/durable "$PAR"
wait_for lists/durable.find
if [ ! -f lists/durable-sami/zdirs.list ]; then
  mk lists/durable.find durable-sami --include sami --exclude sami/gocache --keep --depth 2
fi
./upload.sh $B durable-sami /mnt/durable "$PAR"
[ -f lists/durable-rest/manifest.tsv ] || mk lists/durable.find durable-rest --exclude sami
./upload.sh $B durable-rest /mnt/durable "$PAR"
wait_for lists/nvme-sami.find
[ -f lists/nvme-sami/manifest.tsv ] || mk lists/nvme-sami.find nvme-sami --exclude scratch/devbox-copy
./upload.sh $B nvme-sami /mnt/nvme/sami "$PAR"
wait_for lists/home.find
if [ ! -f lists/home/manifest.tsv ]; then
  ex=(); for x in "${LOGINS[@]}"; do ex+=(--exclude "$x"); done
  mk lists/home.find home "${ex[@]}"
fi
./upload.sh $B home /home/sami "$PAR"
echo "driver done $(date -u +%FT%TZ)" >>driver.log
