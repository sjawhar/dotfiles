#!/usr/bin/env bash
# Complete a find listing that ran as sami: list, as root, the directories it could not read
# (from the unit's journal), append them, and move NAME.find.tmp to NAME.find.
# Usage: finish-find.sh NAME UNIT ROOT   (run in the copy directory, after UNIT ended)
set -eu
NAME=$1 UNIT=$2 ROOT=$3
tmp=lists/$NAME.find.tmp
[ -f "$tmp" ] || { echo "no $tmp" >&2; exit 1; }
journalctl -u "$UNIT" --no-pager -o cat | sed -n "s/^find: ‘\(.*\)’: Permission denied$/\1/p" \
  | sed -e "s#^$ROOT/##" -e 's#^\./##' | sort -u >"lists/$NAME.denied"
cd "$ROOT"
while IFS= read -r d; do
  sudo -n nice -n 19 find "$d" -xdev -mindepth 1 -printf '%y %s %p\0' 2>/dev/null || true
done <"$OLDPWD/lists/$NAME.denied" >>"$OLDPWD/$tmp"
cd "$OLDPWD"
mv "$tmp" "lists/$NAME.find"
echo "$NAME: $(wc -l <"lists/$NAME.denied") unreadable dirs listed as root"
