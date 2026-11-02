#!/usr/bin/env bash
# Upload tar shards of one tree to the GCS relay (dispatch://INFRA-993 step 3). Restartable:
# shards logged "ok" in $LOG are skipped.
# Usage: upload.sh BOX TREE ROOT PARALLEL
# Shard lists (from mkshards.py) are read from $DEVBOX_COPY_LISTS/TREE: a local directory, or a
# gs:// prefix, streamed shard by shard, so nothing large is stored on the old box.
set -u
BOX=$1 TREE=$2 ROOT=$3 PAR=$4
DIR=${DEVBOX_COPY_DIR:-/mnt/nvme/sami/scratch/devbox-copy}
BUCKET=${DEVBOX_COPY_BUCKET:-gs://sami-devbox-copy-usw4}
LISTS=${DEVBOX_COPY_LISTS:-$DIR/lists}/$TREE
LOG=$DIR/upload.log
ERR=$DIR/err/$TREE
mkdir -p "$ERR"
# The permanent exclusion list (LANE-C embargo, 2026-11-02): no run without it.
EXCLUDE=${DEVBOX_COPY_EXCLUDE:-$DIR/EXCLUDE}
FILTER=$(dirname "$(readlink -f "$0")")/exclude-filter.py
[[ -s $EXCLUDE && -x $FILTER ]] || { echo "refusing to start: no exclusion file $EXCLUDE or filter $FILTER" >&2; exit 2; }
export BOX TREE ROOT DIR BUCKET LISTS LOG ERR EXCLUDE FILTER

feed() {  # the NUL-separated list of shard $1
  if [[ $LISTS == gs://* ]]; then gcloud storage cat "$LISTS/$1.list" 2>>"$ERR/$1.gcs"; else cat "$LISTS/$1.list"; fi
}
export -f feed

worker() {
  local shard=$1 obj=$BUCKET/$BOX/$TREE/$1.tar try
  grep -q "^$BOX $TREE $shard ok " "$LOG" 2>/dev/null && return 0
  # One uploader per shard, even when two runs of a tree overlap.
  local lk; exec {lk}>"$ERR/$shard.lock"; flock -n "$lk" || return 0
  grep -q "^$BOX $TREE $shard ok " "$LOG" 2>/dev/null && return 0
  for try in 1 2 3; do
    while [[ "$(cat "$DIR/gate.state" 2>/dev/null)" != OK* ]]; do sleep 30; done
    local t0=$(date +%s)
    : >"$ERR/$shard.gcs"
    # GNU tar: --ignore-failed-read turns unreadable/vanished files into warnings (listed in .err);
    # --sparse stores holes as holes (test trees hold 1 TiB sparse files).
    feed "$shard" | python3 "$FILTER" "$ROOT" "$EXCLUDE" 2>"$ERR/$shard.excluded" \
      | sudo -n nice -n 19 ionice -c3 tar -C "$ROOT" --null --verbatim-files-from --no-recursion \
      --sparse --ignore-failed-read --warning=no-file-changed --warning=no-file-removed \
      -T - -cf - 2>"$ERR/$shard.err" \
      | nice -n 19 gcloud storage cp - "$obj" 2>>"$ERR/$shard.gcs"
    local st=("${PIPESTATUS[@]}")
    if [[ ${st[0]} -eq 0 && ${st[1]} -eq 0 && ${st[2]} -le 1 && ${st[3]} -eq 0 ]]; then
      local bytes=$(gcloud storage ls -l "$obj" 2>/dev/null | awk 'NR==1{print $1}')
      local unread=$(grep -c -E 'Cannot (open|stat|read)|Permission denied' "$ERR/$shard.err")
      local excl=$(grep -c '^EXCLUDED ' "$ERR/$shard.excluded")
      echo "$BOX $TREE $shard ok bytes=${bytes:-?} start=$t0 end=$(date +%s) tar_rc=${st[2]} unreadable=$unread excluded=$excl" >>"$LOG"
      return 0
    fi
    # A failed filter must never let a list through: tar got nothing or a partial list, and the
    # object, if gcloud finalized one, is deleted.
    [[ ${st[1]} -ne 0 ]] && gcloud storage rm "$obj" >/dev/null 2>&1
    echo "$BOX $TREE $shard fail try=$try list_rc=${st[0]} filter_rc=${st[1]} tar_rc=${st[2]} gcs_rc=${st[3]} at=$(date +%s)" >>"$LOG"
    sleep 30
  done
}
export -f worker

# Manifest first, so the puller knows the shard count; the directory shard last.
MAN=$DIR/manifest-$BOX-$TREE.tsv
if [[ $LISTS == gs://* ]]; then gcloud storage cp "$LISTS/manifest.tsv" "$MAN" 2>/dev/null; else cp "$LISTS/manifest.tsv" "$MAN"; fi \
  && gcloud storage cp "$MAN" "$BUCKET/$BOX/$TREE/manifest.tsv" 2>/dev/null \
  || { echo "manifest copy failed for $TREE" >&2; exit 1; }
cut -f1 "$MAN" | grep -v '^zdirs$' | xargs -P "$PAR" -I{} bash -c 'worker {}'
# A --partial list set has no zdirs yet: its tree finishes in a later run.
grep -q '^zdirs' "$MAN" || exit 0
worker zdirs
echo "$BOX $TREE tree-uploaded at=$(date +%s)" >>"$LOG"
