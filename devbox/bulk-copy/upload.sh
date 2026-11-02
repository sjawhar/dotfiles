#!/usr/bin/env bash
# Upload tar shards of one tree to the GCS relay (dispatch://INFRA-993 step 3). Restartable:
# shards logged "ok" in $LOG are skipped.
# Usage: upload.sh BOX TREE ROOT PARALLEL   (lists in $DIR/lists/TREE, from mkshards.py)
set -u
BOX=$1 TREE=$2 ROOT=$3 PAR=$4
DIR=${DEVBOX_COPY_DIR:-/mnt/nvme/sami/scratch/devbox-copy}
BUCKET=${DEVBOX_COPY_BUCKET:-gs://sami-devbox-copy-usw4}
LOG=$DIR/upload.log
ERR=$DIR/err/$TREE
mkdir -p "$ERR"
export BOX TREE ROOT DIR BUCKET LOG ERR

worker() {
  local shard=$1 list=$DIR/lists/$TREE/$1.list obj=$BUCKET/$BOX/$TREE/$1.tar try rc
  grep -q "^$BOX $TREE $shard ok " "$LOG" 2>/dev/null && return 0
  for try in 1 2 3; do
    while [[ "$(cat "$DIR/gate.state" 2>/dev/null)" != OK* ]]; do sleep 30; done
    local t0=$(date +%s)
    # GNU tar: --ignore-failed-read turns unreadable/vanished files into warnings (listed in .err).
    sudo -n nice -n 19 ionice -c3 tar -C "$ROOT" --null --verbatim-files-from --no-recursion \
      --ignore-failed-read --warning=no-file-changed --warning=no-file-removed \
      -T "$list" -cf - 2>"$ERR/$shard.err" \
      | nice -n 19 gcloud storage cp - "$obj" 2>"$ERR/$shard.gcs"
    local st=("${PIPESTATUS[@]}")
    if [[ ${st[0]} -le 1 && ${st[1]} -eq 0 ]]; then
      local bytes=$(gcloud storage ls -l "$obj" 2>/dev/null | awk 'NR==1{print $1}')
      local unread=$(grep -c -E 'Cannot (open|stat|read)|Permission denied' "$ERR/$shard.err")
      echo "$BOX $TREE $shard ok bytes=${bytes:-?} start=$t0 end=$(date +%s) tar_rc=${st[0]} unreadable=$unread" >>"$LOG"
      return 0
    fi
    echo "$BOX $TREE $shard fail try=$try tar_rc=${st[0]} gcs_rc=${st[1]} at=$(date +%s)" >>"$LOG"
    sleep 30
  done
}
export -f worker

# Manifest first, so the puller knows the shard count; the directory shard last.
gcloud storage cp "$DIR/lists/$TREE/manifest.tsv" "$BUCKET/$BOX/$TREE/manifest.tsv" 2>/dev/null \
  || { echo "manifest upload failed for $TREE" >&2; exit 1; }
cut -f1 "$DIR/lists/$TREE/manifest.tsv" | grep -v '^zdirs$' | xargs -P "$PAR" -I{} bash -c 'worker {}'
# A --partial list set has no zdirs yet: its tree finishes in a later run.
[[ -f $DIR/lists/$TREE/zdirs.list ]] || exit 0
worker zdirs
echo "$BOX $TREE tree-uploaded at=$(date +%s)" >>"$LOG"
