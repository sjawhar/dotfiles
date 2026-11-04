#!/usr/bin/env bash
# Wake the copy's owner (dispatch://INFRA-993 step 3): exits with one line naming the first event.
# Checks every 5 minutes; gives up after 2 hours. Run on sami-rocqperf.
D=/mnt/durable/sami/devbox-copy
end=$(( $(date +%s) + 7200 ))
pulled() { timeout 60 ssh -o BatchMode=yes sami@sami-devbox "grep -cE '$1' /mnt/nvme/sami/cutover/bulk-copy.log" 2>/dev/null || true; }
cnt() { grep -c "$1" "$2" 2>/dev/null || true; }
declare -A base
events=("sami-leanperf theorem zdirs ok" "sami-leanperf home zdirs ok" "sami-rocqperf durable-sami zdirs ok" " fail ")
for e in "${events[@]}"; do base[$e]=$(pulled "$e"); done
nvme0=$(cnt "nvme driver start" $D/driver.log)
while (( $(date +%s) < end )); do
  sleep 300
  for e in "${events[@]}"; do
    n=$(pulled "$e"); (( n > ${base[$e]} )) && { echo "EVENT devbox log: $e"; exit 0; }
  done
  (( $(cnt "nvme driver start" $D/driver.log) > nvme0 )) && { echo "EVENT rocqperf nvme/home gate opened"; exit 0; }
  last=$(tail -1 $D/monitor-sami-rocqperf.log)
  [[ $last == *"gate=PAUSE"* ]] && { echo "EVENT durable stop: ${last##*gate=}"; exit 0; }
  for u in devbox-copy-up-rocqperf devbox-copy-up-rocqperf-nvme devbox-copy-monitor; do
    systemctl is-active -q $u || { echo "EVENT rocqperf unit $u is $(systemctl is-active $u)"; exit 0; }
  done
  timeout 60 ssh -o BatchMode=yes sami@sami-devbox 'systemctl is-active -q devbox-copy-pull' || { echo "EVENT devbox-copy-pull not active"; exit 0; }
done
echo "EVENT 2 hours passed"
