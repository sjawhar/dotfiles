#!/usr/bin/env bash
# Download the /mnt/durable snapshot VHD to sami-devbox's local SSD (dispatch://INFRA-993 step 3).
# Only the page blob's valid ranges are fetched (an incremental snapshot is sparse), as ranged GETs,
# 64 at once, written in place into a sparse file. Restartable: finished chunks are in `done`.
# The SAS URL is read from a 0600 file and handed to curl on stdin (-K -), never on a command
# line, in the environment or in a log. No xtrace.
set +x -euo pipefail
SAS_FILE=${SAS_FILE:-/mnt/nvme/sami/cutover/secrets/durable-snapshot-sas.url}
W=${W:-/mnt/scratch/durable-snapshot}
OUT=$W/durable.vhd
LOG=/mnt/nvme/sami/cutover/snapshot-download.log
mkdir -p "$W"
cfg() { printf 'url = "%s%s"\nsilent\nshow-error\nfail\n' "$(cat "$SAS_FILE")" "${1:-}"; }
t0=$(date +%s)
echo "start $(date -u +%FT%TZ)" >>"$LOG"
size=$(cfg | curl -K - -I | awk 'tolower($1)=="content-length:"{print $2}' | tr -d '\r')
[[ $size =~ ^[0-9]+$ ]] || { echo "no size from HEAD" >>"$LOG"; exit 1; }
[[ -f $OUT ]] || truncate -s "$size" "$OUT"
if [[ ! -s $W/ranges ]]; then
  # Get Page Ranges over 8 GiB windows (64 GiB windows got HTTP 500; one window takes 10-60 s),
  # 32 windows at once, one result file per window, so a restart skips finished windows.
  # ranges.tmp/ranges.next hold the windows a first, serial run finished.
  win=$((8 * 1024 ** 3)); mkdir -p "$W/pl"
  first=$(cat "$W/ranges.next" 2>/dev/null || echo 0)
  # pl_get prints the ranges of [s, e]; a window the service answers with 500 (fragmented) is
  # split into 8 and fetched again, down to 4 MiB.
  pl_get() {
    local s=$1 e=$2 xml try n sub k
    for try in 1 2 3; do
      xml=$(cfg "&comp=pagelist" | curl -K - -H "x-ms-version: 2021-08-06" -H "x-ms-range: bytes=$s-$e") && break
      xml=""; sleep $((try * 5))
    done
    if [[ -z $xml ]]; then
      n=$((e - s + 1)); sub=$((n / 8))
      (( sub >= 4 * 1024 ** 2 )) || { echo "pagelist failed at $s ($n bytes)" >>"$LOG"; return 1; }
      for ((k = s; k <= e; k += sub)); do
        pl_get "$k" "$(( k + sub - 1 > e ? e : k + sub - 1 ))" || return 1
      done
      return 0
    fi
    grep -oE '<Start>[0-9]+</Start><End>[0-9]+</End>' <<<"$xml" \
      | sed -E 's#<Start>([0-9]+)</Start><End>([0-9]+)</End>#\1 \2#' || true
  }
  pagelist() {
    [[ -f $W/pl/$1.ok ]] && return 0
    pl_get "$1" "$2" >"$W/pl/$1.part" || return 1
    mv "$W/pl/$1.part" "$W/pl/$1"; touch "$W/pl/$1.ok"
  }
  export -f pagelist pl_get cfg; export W SAS_FILE LOG
  for ((s = first; s < size; s += win)); do e=$((s + win - 1)); (( e >= size )) && e=$((size - 1)); echo "$s $e"; done \
    | xargs -P 32 -L1 bash -c 'pagelist $0 $1'
  for ((s = first; s < size; s += win)); do [[ -f $W/pl/$s.ok ]] || { echo "pagelist incomplete at $s" >>"$LOG"; exit 1; }; done
  { [[ -f $W/ranges.tmp ]] && cat "$W/ranges.tmp"; for ((s = first; s < size; s += win)); do cat "$W/pl/$s"; done; } >"$W/ranges.new"
  mv "$W/ranges.new" "$W/ranges"
fi
used=$(awk '{s += $2 - $1 + 1} END {print s + 0}' "$W/ranges")
echo "size=$size page_ranges=$(wc -l <"$W/ranges") used_bytes=$used" >>"$LOG"
CH=$((256 * 1024 * 1024))
awk -v ch=$CH '{s = $1; e = $2; while (s <= e) { x = s + ch - 1; if (x > e) x = e; print s, x; s = x + 1 } }' \
  "$W/ranges" | sort >"$W/chunks"
# A chunk is fetched to a temporary file and checked for its exact length BEFORE it is written
# into the image. The first run piped curl into dd and read only dd's exit status, so a short or
# failed GET left zeros in the image (seen 2026-11-03: 40 MiB of zeros inside a checksummed file).
fetch() {
  local s=$1 e=$2 try want tmp got
  want=$((e - s + 1)); tmp=$W/part/$s
  for try in 1 2 3 4 5; do
    if cfg | curl -K - -r "$s-$e" -o "$tmp"; then
      got=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
      if [[ $got == "$want" ]]; then
        dd if="$tmp" of="$OUT" bs=4M seek="$s" oflag=seek_bytes conv=notrunc status=none \
          && { rm -f "$tmp"; echo "$s $e" >>"$W/done"; return 0; }
      fi
    fi
    rm -f "$tmp"; sleep $((try * 5))
  done
  echo "FAILED $s $e" >>"$LOG"; return 1
}
export -f fetch cfg; export SAS_FILE OUT LOG W
mkdir -p "$W/part"
touch "$W/done"
# chunks.v2 (snapshot-ranges.py, which follows NextMarker) wins when it exists.
[[ -s $W/chunks.v2 ]] && cp "$W/chunks.v2" "$W/chunks"
sort -n "$W/done" | comm -23 <(sort -n "$W/chunks") - | xargs -P "${PAR:-64}" -L1 bash -c 'fetch $0 $1' || true
t1=$(date +%s)
left=$(sort "$W/done" | comm -23 "$W/chunks" - | wc -l)
echo "end $(date -u +%FT%TZ) seconds=$((t1 - t0)) MiB/s=$((used / 1048576 / (t1 - t0 + 1))) chunks_left=$left" >>"$LOG"
(( left == 0 ))
