#!/usr/bin/env bash
# Copy guard (dispatch://INFRA-993 step 3). Every 60 s: log IO numbers, check disk floors on this
# box and on sami-devbox, write $DIR/gate.state ("OK" or "PAUSE <why>").
# A disk-floor breach also freezes the running upload units at once; they thaw when it clears.
# IO pressure over $IO_LIMIT (some avg60) for 3 samples pauses new shards only.
# Usage: monitor.sh DEVICE   (source data disk to sample, e.g. nvme0n2 or md127)
set -u
DEV=$1
DIR=${DEVBOX_COPY_DIR:-/mnt/nvme/sami/scratch/devbox-copy}
IO_LIMIT=${IO_LIMIT:-50}
HOST=$(hostname)
declare -A LOCAL_FLOORS
if [[ $HOST == sami-rocqperf ]]; then
  LOCAL_FLOORS=([/]=$((18 * 1024 ** 3)) [/mnt/nvme]=84000000000)
fi
DEVBOX_FLOORS="/:$((50 * 1024 ** 3)) /mnt/nvme:$((400 * 1024 ** 3)) /mnt/scratch:$((500 * 1024 ** 3)) /mnt/durable:$((200 * 1024 ** 3))"
high=0 frozen=0
read_stats() { awk -v d="$DEV" '$3==d{print $4, $6, $8, $10, $13}' /proc/diskstats; }
prev=($(read_stats)); pt=$(date +%s)
while :; do
  sleep 60
  now=$(date +%s); cur=($(read_stats)); dt=$((now - pt))
  rs=$(( (cur[0] - prev[0]) / dt )); rmb=$(( (cur[1] - prev[1]) * 512 / dt / 1000000 ))
  ws=$(( (cur[2] - prev[2]) / dt )); wmb=$(( (cur[3] - prev[3]) * 512 / dt / 1000000 ))
  util=$(( (cur[4] - prev[4]) / dt / 10 ))
  prev=("${cur[@]}"); pt=$now
  psome=$(awk '/^some/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  pfull=$(awk '/^full/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  load=$(cut -d' ' -f1 /proc/loadavg)
  why=""
  for m in "${!LOCAL_FLOORS[@]}"; do
    free=$(df -B1 --output=avail "$m" | tail -1); fl=${LOCAL_FLOORS[$m]}
    (( free < fl )) && why+="floor:$HOST:$m=$free "
  done
  if [[ $HOST == sami-rocqperf ]] && [[ -f /mnt/nvme/sami/tmp/disk-fill-watch/state.json ]]; then
    inc=$(jq -r '.incidents | keys | join(",")' /mnt/nvme/sami/tmp/disk-fill-watch/state.json 2>/dev/null)
    [[ -n $inc ]] && why+="disk-fill-watch:$inc "
  fi
  dv=$(ssh -o BatchMode=yes -o ConnectTimeout=20 sami@sami-devbox \
    'df -B1 --output=target,avail / /mnt/nvme /mnt/scratch /mnt/durable | tail -n +2' 2>/dev/null)
  if [[ -z $dv ]]; then
    why+="devbox-unreachable "
  else
    for f in $DEVBOX_FLOORS; do
      m=${f%%:*} fl=${f##*:}
      free=$(awk -v m="$m" '$1==m{print $2}' <<<"$dv")
      [[ -n $free ]] && (( free < fl )) && why+="floor:sami-devbox:$m=$free "
    done
  fi
  # Freeze at once on a floor breach; thaw when clear.
  units=$(systemctl list-units --plain --no-legend 'devbox-copy-up-*' | awk '{print $1}')
  if [[ $why == *floor* || $why == *disk-fill-watch* ]]; then
    for u in $units; do sudo -n systemctl freeze "$u" 2>/dev/null; done; frozen=1
  elif (( frozen )); then
    for u in $units; do sudo -n systemctl thaw "$u" 2>/dev/null; done; frozen=0
  fi
  if awk -v p="$psome" -v l="$IO_LIMIT" 'BEGIN{exit !(p>l)}'; then high=$((high + 1)); else high=0; fi
  (( high >= 3 )) && why+="io-pressure:$psome "
  if [[ -z $why ]]; then echo OK >"$DIR/gate.state.tmp"; else echo "PAUSE $why" >"$DIR/gate.state.tmp"; fi
  mv "$DIR/gate.state.tmp" "$DIR/gate.state"
  echo "$(date -u +%FT%TZ) $DEV r/s=$rs rMB/s=$rmb w/s=$ws wMB/s=$wmb util=$util% io_some60=$psome io_full60=$pfull load=$load gate=$(cat "$DIR/gate.state")" >>"$DIR/monitor-$HOST.log"
done
