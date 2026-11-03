#!/usr/bin/env bash
# Run one rocqperf -> sami-devbox delta pass from a plan (delta-plan.py), tree by tree
# (dispatch://INFRA-993 step 3). Run on sami-rocqperf, as sami (sudo for the reads).
# rsync -aHAX --numeric-ids --update, no --delete, no --inplace; the receiver runs as root under
# cgroup-guard. EXCLUDE and the scope files become rsync excludes. Before each tree it waits while
# the copy monitor holds the disks (durable trees: nvme0n2 busy; others: md127 read wait or floors).
# Usage: delta-run.sh PLAN.tsv DEST_ROOT_MAP   e.g. delta-run.sh plan.tsv /mnt/nvme/sami=/mnt/nvme/sami
set -u
PLAN=$1 MAP=$2
D=/mnt/durable/sami/devbox-copy
SRC_ROOT=${MAP%%=*} DST_ROOT=${MAP#*=}
LOG=$D/delta-$(date -u +%Y%m%dT%H%MZ)-$(basename "$SRC_ROOT").log
EXF=$(mktemp)
# Rule files -> rsync filters, anchored at SRC_ROOT.
for f in $D/EXCLUDE $D/SCOPE-base-nvme-sami $D/SCOPE-nvme-sami; do
  [[ -f $f ]] || continue
  while read -r kind arg; do
    case $kind in
      path) [[ $arg == "$SRC_ROOT"/* ]] && echo "- /${arg#"$SRC_ROOT"/}" ;;
      component) echo "- $arg" ;;
      name) echo "- *$arg*" ;;
    esac
  done < <(grep -vE '^\s*(#|$)' "$f")
done >"$EXF"
held() {
  local last; last=$(tail -1 $D/monitor-sami-rocqperf.log)
  if [[ $SRC_ROOT == /mnt/durable* ]]; then [[ $last == *"dheld=1"* ]]
  else [[ $last == *"nheld=1"* || $last == *"lfloor=1"* ]]; fi
}
total=0
while IFS=$'\t' read -r act path why; do
  [[ $act == copy ]] || { echo "skip $path ($why)" >>"$LOG"; continue; }
  while held; do sleep 60; done
  rel=${path#"$SRC_ROOT"/}
  echo "start $rel $(date -u +%FT%TZ)" >>"$LOG"
  # A directory copies as dir/ into dir/; a single file (e.g. /mnt/durable/README.md) as itself.
  if [[ -d $path && ! -L $path ]]; then s="$path/" t="$DST_ROOT/$rel/"; else s="$path" t="$DST_ROOT/$rel"; fi
  out=$(sudo -n nice -n 19 ionice -c3 rsync -aHAX --numeric-ids --update --mkpath --stats ${BWLIMIT:+--bwlimit=$BWLIMIT} --filter="merge $EXF" \
    -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -l sami" \
    --rsync-path="sudo /mnt/nvme/sami/cutover/cgroup-guard.sh run -- rsync" \
    "$s" "sami-devbox:$t" 2>&1); rc=$?
  b=$(sed -n 's/^Total transferred file size: \([0-9,]*\) bytes/\1/p' <<<"$out" | tr -d ,)
  n=$(sed -n 's/^Number of regular files transferred: \([0-9,]*\)/\1/p' <<<"$out" | tr -d ,)
  total=$((total + ${b:-0}))
  echo "end $rel rc=$rc files=${n:-?} bytes=${b:-?} $(date -u +%FT%TZ)" >>"$LOG"
  (( rc == 0 || rc == 23 || rc == 24 )) || grep -E 'rsync error|failed' <<<"$out" | tail -3 >>"$LOG"
done <"$PLAN"
echo "pass total bytes=$total $(date -u +%FT%TZ)" >>"$LOG"
rm -f "$EXF"
echo "$LOG"
