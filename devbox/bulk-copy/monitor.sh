#!/usr/bin/env bash
# Copy guard (dispatch://INFRA-993 step 3). Every 60 s: log IO numbers, check disk floors on this
# box and on sami-devbox, write $DIR/gate.state ("OK" or "PAUSE <why>").
# A disk-floor breach freezes the running upload units at once; they thaw when it clears.
# IO pressure over $IO_LIMIT (some avg60) for 3 samples pauses new shards; for 5 samples it also
# freezes the upload units, until it falls back under the limit.
# Usage: monitor.sh DEVICE...   (source data disks to sample, e.g. nvme0n2 md127)
set -u
DEVS=("$@")
DIR=${DEVBOX_COPY_DIR:-/mnt/nvme/sami/scratch/devbox-copy}
IO_LIMIT=${IO_LIMIT:-50}
HOST=$(hostname)
declare -A LOCAL_FLOORS
if [[ $HOST == sami-rocqperf ]]; then
  LOCAL_FLOORS=([/]=$((18 * 1024 ** 3)) [/mnt/nvme]=84000000000)
fi
DEVBOX_FLOORS="/:$((50 * 1024 ** 3)) /mnt/nvme:$((400 * 1024 ** 3)) /mnt/scratch:$((500 * 1024 ** 3)) /mnt/durable:$((200 * 1024 ** 3))"
high=0 frozen=1  # 1: the first clear sample thaws units a previous run froze
read_stats() { awk -v d="$1" '$3==d{print $4, $6, $8, $10, $13}' /proc/diskstats; }
declare -A PREV
for d in "${DEVS[@]}"; do PREV[$d]=$(read_stats "$d"); done
pt=$(date +%s)
while :; do
  sleep 60
  now=$(date +%s); dt=$((now - pt)); pt=$now; stats=""
  for d in "${DEVS[@]}"; do
    cur=($(read_stats "$d")); prev=(${PREV[$d]}); PREV[$d]="${cur[*]}"
    stats+="$d r/s=$(( (cur[0] - prev[0]) / dt )) rMB/s=$(( (cur[1] - prev[1]) * 512 / dt / 1000000 ))"
    stats+=" w/s=$(( (cur[2] - prev[2]) / dt )) wMB/s=$(( (cur[3] - prev[3]) * 512 / dt / 1000000 ))"
    stats+=" util=$(( (cur[4] - prev[4]) / dt / 10 ))% "
  done
  psome=$(awk '/^some/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  pfull=$(awk '/^full/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  load=$(cut -d' ' -f1 /proc/loadavg)
  why=""
  for m in "${!LOCAL_FLOORS[@]}"; do
    free=$(df -B1 --output=avail "$m" | tail -1); fl=${LOCAL_FLOORS[$m]}
    (( free < fl )) && why+="floor:$HOST:$m=$free "
  done
  # disk-fill-watch keeps an incident open until free space is 1.5x the floor; DFW_INCIDENTS=0
  # gates on the floors alone (Main, 2026-11-02 18:44Z: resume once /mnt/nvme is above 84 GB).
  if [[ ${DFW_INCIDENTS:-1} == 1 && $HOST == sami-rocqperf ]] && [[ -f /mnt/nvme/sami/tmp/disk-fill-watch/state.json ]]; then
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
  if awk -v p="$psome" -v l="$IO_LIMIT" 'BEGIN{exit !(p>l)}'; then high=$((high + 1)); else high=0; fi
  (( high >= 3 )) && why+="io-pressure:$psome "
  (( high >= 5 )) && why+="io-pressure-5min "
  # Freeze at once on a floor breach or 5 minutes of high IO pressure; thaw when clear.
  units=$(systemctl list-units --plain --no-legend 'devbox-copy-up-*' 'devbox-copy-find-*' | awk '{print $1}')
  if [[ $why == *floor* || $why == *disk-fill-watch* || $why == *io-pressure-5min* ]]; then
    for u in $units; do sudo -n systemctl freeze "$u" 2>/dev/null; done; frozen=1
  elif (( frozen )); then
    for u in $units; do sudo -n systemctl thaw "$u" 2>/dev/null; done; frozen=0
  fi
  if [[ -z $why ]]; then echo OK >"$DIR/gate.state.tmp"; else echo "PAUSE $why" >"$DIR/gate.state.tmp"; fi
  mv "$DIR/gate.state.tmp" "$DIR/gate.state"
  echo "$(date -u +%FT%TZ) ${stats}io_some60=$psome io_full60=$pfull load=$load frozen=$frozen gate=$(cat "$DIR/gate.state")" >>"$DIR/monitor-$HOST.log"
done
