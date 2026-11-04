#!/usr/bin/env bash
# Copy guard (dispatch://INFRA-993 step 3). Every 60 s: log IO numbers, check disk floors on this
# box and on sami-devbox, write $DIR/gate.state ("OK" or "PAUSE <why>"), freeze or thaw units.
# Two classes of copy units (Main, 2026-11-02 ~21:10Z, decision b):
#   DUR_UNITS (the capped /mnt/durable copy and its listing): frozen only by a sami-devbox floor or
#     io some avg60 above $DUR_IO_LIMIT (95) for 5 samples. Local floors do not stop it.
#   every other devbox-copy-up-* unit (/mnt/nvme/sami, /home/sami, leanperf): frozen by any floor,
#     a disk-fill-watch incident (DFW_INCIDENTS=1), or io above $IO_LIMIT (50) for 5 samples.
# gate.state (read by upload.sh workers before each shard) follows the DUR rule; the log line's
# lfloor=1 marks a local floor breach, which the /mnt/nvme driver also refuses to start under.
# Usage: monitor.sh DEVICE...   (data disks to sample, e.g. nvme0n2 md127)
set -u
DEVS=("$@")
DIR=${DEVBOX_COPY_DIR:-/mnt/nvme/sami/scratch/devbox-copy}
IO_LIMIT=${IO_LIMIT:-50}
DUR_IO_LIMIT=${DUR_IO_LIMIT:-95}
DUR_UNITS=${DUR_UNITS:-"devbox-copy-up-rocqperf.service devbox-copy-find-durable.service"}
HOST=$(hostname)
declare -A LOCAL_FLOORS
if [[ $HOST == sami-rocqperf ]]; then
  LOCAL_FLOORS=([/]=$((18 * 1024 ** 3)) [/mnt/nvme]=84000000000)
fi
DEVBOX_FLOORS="/:$((50 * 1024 ** 3)) /mnt/nvme:$((400 * 1024 ** 3)) /mnt/scratch:$((500 * 1024 ** 3)) /mnt/durable:$((200 * 1024 ** 3))"
high=0 dhigh=0
# Disk rule for the durable units (Main, 2026-11-03 03:40Z; await only since 2026-11-04 01:0xZ):
# freeze when DUR_DEV's await is above 20 ms for 2 samples; thaw after 2 samples under. It starts
# held. %util is not used: on this Azure disk the lanes keep it at 80-100% with await at 2-10 ms
# (119 of 120 samples on 2026-11-03 23:00-01:00Z), so it held the copy all night.
DUR_DEV=${DUR_DEV:-nvme0n2} dbusy=0 dcalm=0 dheld=1
# Read-wait rule for the other copy units (Main, 2026-11-03 ~04:40Z): freeze when the read wait
# of any disk they read (NV_DEVS: md127's members and the root disk) is above 20 ms for 2 samples;
# thaw after 2 samples under. md127 itself reports no wait, so its members are sampled.
# The devices come from $DIR/nv_devs when it exists (the driver writes the disks of the tree it
# copies: md127's members for /mnt/nvme/sami, nvme0n1 for /home); else NV_DEVS.
NV_DEVS=${NV_DEVS:-"nvme1n1 nvme2n1 nvme3n1 nvme4n1"} nbusy=0 ncalm=0 nheld=1
read_stats() { awk -v d="$1" '$3==d{print $4, $6, $8, $10, $13, $7, $11}' /proc/diskstats; }
declare -A PREV
for d in "${DEVS[@]}"; do PREV[$d]=$(read_stats "$d"); done
pt=$(date +%s)
while :; do
  sleep 60
  now=$(date +%s); dt=$((now - pt)); pt=$now; stats=""
  nmax=0; [[ -s $DIR/nv_devs ]] && NV_DEVS=$(cat "$DIR/nv_devs")
  for d in "${DEVS[@]}"; do
    cur=($(read_stats "$d")); prev=(${PREV[$d]}); PREV[$d]="${cur[*]}"
    ios=$(( cur[0] - prev[0] + cur[2] - prev[2] ))
    aw=$(( ios > 0 ? (cur[5] - prev[5] + cur[6] - prev[6]) / ios : 0 ))
    ut=$(( (cur[4] - prev[4]) / dt / 10 ))
    stats+="$d r/s=$(( (cur[0] - prev[0]) / dt )) rMB/s=$(( (cur[1] - prev[1]) * 512 / dt / 1000000 ))"
    stats+=" w/s=$(( (cur[2] - prev[2]) / dt )) wMB/s=$(( (cur[3] - prev[3]) * 512 / dt / 1000000 ))"
    stats+=" await=${aw}ms util=${ut}% "
    if [[ $d == "$DUR_DEV" ]]; then
      if (( aw > 20 )); then dbusy=$((dbusy + 1)); dcalm=0; else dcalm=$((dcalm + 1)); dbusy=0; fi
      (( dbusy >= 2 )) && dheld=1
      (( dcalm >= ${DUR_CALM:-2} )) && dheld=0
    fi
    if [[ " $NV_DEVS " == *" $d "* ]]; then
      rd=$(( cur[0] - prev[0] )); raw=$(( rd > 0 ? (cur[5] - prev[5]) / rd : 0 ))
      (( raw > nmax )) && nmax=$raw
    fi
  done
  if (( nmax > 20 )); then nbusy=$((nbusy + 1)); ncalm=0; else ncalm=$((ncalm + 1)); nbusy=0; fi
  (( nbusy >= 2 )) && nheld=1
  (( ncalm >= 2 )) && nheld=0
  psome=$(awk '/^some/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  pfull=$(awk '/^full/{split($3,a,"=");print a[2]}' /proc/pressure/io)
  load=$(cut -d' ' -f1 /proc/loadavg)
  lwhy="" dwhy=""
  for m in "${!LOCAL_FLOORS[@]}"; do
    free=$(df -B1 --output=avail "$m" | tail -1); fl=${LOCAL_FLOORS[$m]}
    (( free < fl )) && lwhy+="floor:$HOST:$m=$free "
  done
  if [[ ${DFW_INCIDENTS:-1} == 1 && $HOST == sami-rocqperf ]] && [[ -f /mnt/nvme/sami/tmp/disk-fill-watch/state.json ]]; then
    inc=$(jq -r '.incidents | keys | join(",")' /mnt/nvme/sami/tmp/disk-fill-watch/state.json 2>/dev/null)
    [[ -n $inc ]] && lwhy+="disk-fill-watch:$inc "
  fi
  dv=$(ssh -o BatchMode=yes -o ConnectTimeout=20 sami@sami-devbox \
    'df -B1 --output=target,avail / /mnt/nvme /mnt/scratch /mnt/durable | tail -n +2' 2>/dev/null)
  if [[ -z $dv ]]; then
    dwhy+="devbox-unreachable "
  else
    for f in $DEVBOX_FLOORS; do
      m=${f%%:*} fl=${f##*:}
      free=$(awk -v m="$m" '$1==m{print $2}' <<<"$dv")
      [[ -n $free ]] && (( free < fl )) && dwhy+="floor:sami-devbox:$m=$free "
    done
  fi
  awk -v p="$psome" -v l="$IO_LIMIT" 'BEGIN{exit !(p>l)}' && high=$((high + 1)) || high=0
  awk -v p="$psome" -v l="$DUR_IO_LIMIT" 'BEGIN{exit !(p>l)}' && dhigh=$((dhigh + 1)) || dhigh=0
  (( dhigh >= 5 )) && dwhy+="io-pressure-5min>$DUR_IO_LIMIT:$psome "
  # The disk rule freezes only the durable units; it does not touch gate.state, which the
  # /mnt/nvme/sami workers also read (they read md127, not nvme0n2).
  units=$(systemctl list-units --plain --no-legend 'devbox-copy-up-*' 'devbox-copy-find-*' | awk '{print $1}')
  for u in $units; do
    if [[ " $DUR_UNITS " == *" $u "* ]]; then
      stop=$([[ -n $dwhy && $dwhy != devbox-unreachable\  ]] || (( dheld )) && echo 1 || echo 0)
    else
      stop=$([[ -n $lwhy || $dwhy == *floor* ]] || (( high >= 5 || nheld )) && echo 1 || echo 0)
    fi
    if (( stop )); then sudo -n systemctl freeze "$u" 2>/dev/null; else sudo -n systemctl thaw "$u" 2>/dev/null; fi
  done
  # $DIR/HOLD (any content, a reason) pauses every new shard; shards in flight finish.
  [[ -s $DIR/HOLD ]] && dwhy+="hold:$(head -c 80 "$DIR/HOLD" | tr -s ' \n' '_') "
  if [[ -z $dwhy ]]; then echo OK >"$DIR/gate.state.tmp"; else echo "PAUSE $dwhy" >"$DIR/gate.state.tmp"; fi
  mv "$DIR/gate.state.tmp" "$DIR/gate.state"
  echo "$(date -u +%FT%TZ) ${stats}io_some60=$psome io_full60=$pfull load=$load lfloor=$([[ -n $lwhy ]] && echo 1 || echo 0) high50=$high high95=$dhigh dbusy=$dbusy dcalm=$dcalm dheld=$dheld nv_rawait_max=${nmax}ms nheld=$nheld gate=$(cat "$DIR/gate.state") local=[$lwhy]" >>"$DIR/monitor-$HOST.log"
done
