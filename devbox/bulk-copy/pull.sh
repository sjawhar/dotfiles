#!/usr/bin/env bash
# sami-devbox side of the GCS relay (dispatch://INFRA-993 step 3). Restartable.
# Reads $TREES (lines: BOX TREE TARGET). For each finished shard object: stream-extract it into
# TARGET as root (owners kept by number), log it in $LOG, delete the object after a clean extract.
# A tree's zdirs shard (directory owners, modes, mtimes) is extracted after all its file shards.
# Usage: pull.sh PARALLEL TREES_FILE   (one puller per source box)
set -u
PAR=$1
BUCKET=${DEVBOX_COPY_BUCKET:-gs://sami-devbox-copy-usw4}
CUT=/mnt/nvme/sami/cutover
LOG=$CUT/bulk-copy.log
TREES=$2
ERR=$CUT/pull-err
mkdir -p "$ERR"
export BUCKET LOG ERR CUT

floors_ok() {
  local m fl free
  for f in /:$((50 * 1024 ** 3)) /mnt/nvme:$((400 * 1024 ** 3)) /mnt/scratch:$((500 * 1024 ** 3)) /mnt/durable:$((200 * 1024 ** 3)); do
    m=${f%%:*} fl=${f##*:}
    free=$(df -B1 --output=avail "$m" | tail -1)
    (( free >= fl )) || { echo "floor $m=$free"; return 1; }
  done
}
export -f floors_ok

process() {  # BOX TREE TARGET SHARD
  local box=$1 tree=$2 target=$3 shard=$4 obj=$BUCKET/$1/$2/$4.tar
  grep -q "^$box $tree $shard ok " "$LOG" 2>/dev/null && return 0
  while ! floors_ok >/dev/null; do sleep 60; done
  local bytes=$(gcloud storage ls -l "$obj" 2>/dev/null | awk 'NR==1{print $1}')
  local t0=$(date +%s)
  sudo mkdir -p "$target"
  # tar -v names go to fix-parents.py, which gives directories tar made without a shard entry
  # (root-owned) the owner of their nearest real parent; the zdirs shard sets the rest at the end.
  gcloud storage cp "$obj" - 2>"$ERR/$tree.$shard.gcs" \
    | sudo tar -C "$target" -xpvf - --numeric-owner 2>"$ERR/$tree.$shard.err" \
    | sudo python3 "$CUT/fix-parents.py" "$target" "$t0" >"$ERR/$tree.$shard.parents"
  local st=("${PIPESTATUS[@]}")
  if [[ ${st[0]} -eq 0 && ${st[1]} -eq 0 && ${st[2]} -eq 0 ]]; then
    gcloud storage rm "$obj" >/dev/null 2>&1
    echo "$box $tree $shard ok bytes=${bytes:-?} start=$t0 end=$(date +%s) parents_fixed=$(cat "$ERR/$tree.$shard.parents")" >>"$LOG"
  else
    echo "$box $tree $shard fail gcs_rc=${st[0]} tar_rc=${st[1]} fix_rc=${st[2]} bytes=${bytes:-?} start=$t0 end=$(date +%s)" >>"$LOG"
  fi
}
export -f process

while :; do
  while read -r box tree target; do
    [[ -z $box || $box == \#* ]] && continue
    grep -q "^$box $tree zdirs ok " "$LOG" 2>/dev/null && continue
    objs=$(gcloud storage ls "$BUCKET/$box/$tree/" 2>/dev/null)
    [[ -z $objs ]] && continue
    pending=$(grep -oE 's[0-9]{4}\.tar$' <<<"$objs" | sed 's/\.tar$//' | while read -r s; do
      grep -q "^$box $tree $s ok " "$LOG" 2>/dev/null || echo "$s"; done)
    [[ -n $pending ]] && xargs -P "$PAR" -I{} bash -c "process '$box' '$tree' '$target' {}" <<<"$pending"
    nfile=$(gcloud storage cat "$BUCKET/$box/$tree/manifest.tsv" 2>/dev/null | grep -c '^s')
    ndone=$(grep -cE "^$box $tree s[0-9]{4} ok " "$LOG")
    if grep -q 'zdirs\.tar$' <<<"$objs" && (( nfile > 0 && ndone >= nfile )); then
      process "$box" "$tree" "$target" zdirs
    fi
  done <"$TREES"
  sleep 30
done
