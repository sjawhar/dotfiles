#!/usr/bin/env bash
# sami-leanperf upload order (dispatch://INFRA-993 step 3): the two local-NVMe trees first,
# in parallel, then /theorem, then the home (to the stage). Run in ~/devbox-copy.
cd ~/devbox-copy || exit 1
export DEVBOX_COPY_DIR=$HOME/devbox-copy
./upload.sh sami-leanperf theorem-xfs /theorem-xfs 24 &
while [ ! -f lists/theorem-ext4.find ]; do sleep 30; done
[ -f lists/theorem-ext4/manifest.tsv ] || nice -n 19 python3 mkshards.py lists/theorem-ext4.find theorem-ext4 >>driver.log 2>&1
./upload.sh sami-leanperf theorem-ext4 /theorem-ext4 16
wait
./upload.sh sami-leanperf theorem /theorem 32
./upload.sh sami-leanperf home /home/sami 16
echo "driver done $(date -u +%FT%TZ)" >>driver.log
