#!/usr/bin/env bash
# Copy rocqperf's /mnt/durable from the downloaded snapshot image to sami-devbox /mnt/durable
# (dispatch://INFRA-993 step 3). Run as root on sami-devbox, after snapshot-download.sh ended.
#   mount  attach the image read-only (no journal replay: crash-consistent, ro,noload)
#   copy   list, filter (EXCLUDE + scope rules), no-offbox check, rsync into /mnt/durable
#   verify per-group file counts and bytes, list vs target, and an rsync dry run
#   umount detach
set -euo pipefail
C=/mnt/nvme/sami/cutover
IMG=${IMG:-/mnt/scratch/durable-snapshot/durable.vhd}
MNT=/mnt/durable-snap
L=$C/snapshot-copy
mkdir -p "$L"
case ${1:-} in
mount)
  size=$(stat -c %s "$IMG")
  dev=$(losetup -r --sizelimit $((size - 512)) -f --show "$IMG")   # a fixed VHD ends in a 512-byte footer
  echo "$dev" >"$L/loopdev"
  blkid "$dev" | tee "$L/blkid"
  mkdir -p "$MNT"
  mount -o ro,noload "$dev" "$MNT"
  df -h "$MNT" | tee -a "$L/blkid" ;;
copy)
  cd "$MNT"
  find . -xdev \( -path ./lost+found -o -path ./swapfile-overflow -o -path ./sami/devbox-copy -o -path ./sami/gocache \) -prune \
    -o -mindepth 1 -printf '%y %s %P\0' >"$L/all.find"
  python3 "$C/exclude-filter.py" /mnt/durable "$C/EXCLUDE" --records <"$L/all.find" >"$L/kept.find" 2>"$L/excluded.txt"
  tr '\0' '\n' <"$L/kept.find" | cut -d' ' -f3- | tr '\n' '\0' >"$L/kept.list"
  if ! "${NO_OFFBOX:-/home/sami/.dotfiles/scripts/no-offbox}" --root "$MNT" --from "$L/kept.list" -0 >"$L/offbox.txt" 2>&1; then
    echo "no-offbox refused entries; see $L/offbox.txt" >&2
    # Drop the refused paths (and everything under them) from the list, keep the record.
    grep -oE 'REFUSED [^:]+' "$L/offbox.txt" | cut -d' ' -f2- | sed "s#^$MNT/##" | sort -u >"$L/refused.txt"
    awk '{print "path /mnt/durable/" $0}' "$L/refused.txt" >"$L/refused.rules"
    python3 "$C/exclude-filter.py" /mnt/durable "$C/EXCLUDE" "$L/refused.rules" <"$L/kept.list" >"$L/kept.list.ok" 2>>"$L/excluded.txt"
    mv "$L/kept.list.ok" "$L/kept.list"
  fi
  rsync -aHAX --numeric-ids --inplace --from0 --files-from="$L/kept.list" "$MNT/" /mnt/durable/ \
    --info=stats2 >"$L/rsync.txt" 2>"$L/rsync.err" || echo "rsync rc=$?" >>"$L/rsync.err" ;;
verify)
  # Expected: from the kept listing, per depth-2 group (files = non-directories, bytes = regular files).
  python3 - "$L/kept.find" "$L/kept.list" "$L/expected.tsv" <<'PY'
import collections, sys
keep = set(x for x in open(sys.argv[2], "rb").read().split(b"\0") if x)
g = collections.defaultdict(lambda: [0, 0])
for rec in open(sys.argv[1], "rb").read().split(b"\0"):
    if not rec: continue
    k, s, p = rec.split(b" ", 2)
    if p not in keep or k == b"d": continue
    key = b"/".join(p.split(b"/")[:2]).decode("utf-8", "surrogateescape")
    g[key][0] += 1; g[key][1] += int(s) if k == b"f" else 0
with open(sys.argv[3], "w", encoding="utf-8", errors="surrogateescape") as fh:
    for key in sorted(g): fh.write(f"{key}\t{g[key][0]}\t{g[key][1]}\n")
PY
  python3 "$C/verify.py" "$L/expected.tsv" /mnt/durable --jobs 64 >"$L/verify.txt" || true
  rsync -aHAXn --numeric-ids --from0 --files-from="$L/kept.list" --itemize-changes "$MNT/" /mnt/durable/ >"$L/dryrun.txt" 2>&1 || true
  echo "verify: $(grep ^TOTAL "$L/verify.txt"); dry-run changes: $(grep -c '^[<>ch.]' "$L/dryrun.txt")" ;;
umount)
  umount "$MNT"; losetup -d "$(cat "$L/loopdev")" ;;
*) echo "usage: $0 mount|copy|verify|umount" >&2; exit 2 ;;
esac
