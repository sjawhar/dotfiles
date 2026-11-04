#!/usr/bin/env bash
# sami-rocqperf /mnt/durable upload (dispatch://INFRA-993 step 3): /mnt/durable/sami first (a
# --partial shard set made while the listing ran goes first, the rest after the listing ends),
# then the rest of /mnt/durable. Run in a unit with io.max on nvme0n2. PAR from $1 (default 16).
# Every file the copy writes on rocqperf is under $DEVBOX_COPY_DIR on /mnt/durable; /mnt/nvme/sami
# and /home/sami have their own units with lists streamed from the bucket (see sami-devbox.md).
set -u
export DEVBOX_COPY_DIR=${DEVBOX_COPY_DIR:-/mnt/durable/sami/devbox-copy}
cd "$DEVBOX_COPY_DIR" || exit 1
PAR=${1:-16}
B=sami-rocqperf
mk() { nice -n 19 python3 mkshards.py "$@" >>driver.log 2>&1; }

[ -f lists/durable-sami/manifest.tsv ] && ./upload.sh $B durable-sami /mnt/durable "$PAR"
while [ ! -f lists/durable.find ]; do sleep 60; done
if ! grep -q '^zdirs' lists/durable-sami/manifest.tsv; then
  mk lists/durable.find durable-sami --include sami --exclude sami/gocache --exclude sami/devbox-copy --keep --depth 2
fi
./upload.sh $B durable-sami /mnt/durable "$PAR"
if [ ! -f lists/durable-rest/manifest.tsv ]; then
  mk lists/durable.find durable-rest --exclude sami
fi
./upload.sh $B durable-rest /mnt/durable "$PAR"
echo "durable driver done $(date -u +%FT%TZ)" >>driver.log
