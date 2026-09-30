#!/usr/bin/env bash
# Report which SDD gates each PR has at its CURRENT head, so a missing gate is
# found before a packet rather than by the merge queue.
#
# The six gates (see plugins/sjawhar/commands/sdd.md):
#   1 reviewer  2 thermonuclear-deep-review  3 thermonuclear-code-quality
#   4 acceptance (tester)  5 simplify record  6 queue's own gate (not ours)
#
# A gate counts only when its comment names the current head, because a verdict
# at an older head says nothing about the code that would merge.
#
# Usage:
#   pr-gates.sh                     # every open PR authored by the current user
#   pr-gates.sh 1611 1612 1620      # named PRs
#   REPO=owner/name pr-gates.sh     # another repo (default sjawhar/legion)
set -euo pipefail

repo=${REPO:-sjawhar/legion}

prs=("$@")
if [ ${#prs[@]} -eq 0 ]; then
  mapfile -t prs < <(gh pr list -R "$repo" --author @me --state open --limit 100 --json number --jq '.[].number')
fi

if [ ${#prs[@]} -eq 0 ]; then
  echo "no open PRs"
  exit 0
fi

printf '%-7s %-9s %-14s %-4s %-4s %-4s %-4s %-4s  %s\n' \
  PR HEAD MERGEABLE REV DEEP QUAL ACPT SIMP MISSING

for n in "${prs[@]}"; do
  meta=$(gh pr view "$n" -R "$repo" --json headRefOid,mergeable,mergeStateStatus 2>/dev/null) || {
    printf '%-7s %s\n' "#$n" "(unreadable)"; continue; }
  head=$(jq -r .headRefOid <<<"$meta")
  short=${head:0:8}
  mergeable=$(jq -r '.mergeable + "/" + .mergeStateStatus' <<<"$meta")

  # First line of every comment, plus its author, so a verdict can be attributed.
  bodies=$(gh api "repos/$repo/issues/$n/comments" --paginate \
    --jq '.[] | (.body | split("\n")[0])' 2>/dev/null || true)

  # A gate is present only when its line names this head. Verdict lines are
  # "Verdict: <MERGE|CHANGES> at <sha>"; acceptance "Acceptance: <PASS|FAIL> at <sha>";
  # simplify "Simplify record at <sha>". Reviewer, deep and quality all emit
  # "Verdict:", so they cannot be told apart from the first line alone — the count
  # of distinct verdict lines at this head is reported instead.
  at_head=$(grep -F "$head" <<<"$bodies" || true)
  verdicts=$(grep -c '^Verdict:' <<<"$at_head" || true)
  acpt=$(grep -c '^Acceptance:' <<<"$at_head" || true)
  simp=$(grep -ci '^Simplify' <<<"$at_head" || true)

  # Report the verdict count in the three review columns: three distinct verdict
  # comments at the head means reviewer + deep + quality have all judged it.
  rev=$([ "${verdicts:-0}" -ge 1 ] && echo yes || echo NO)
  deep=$([ "${verdicts:-0}" -ge 2 ] && echo yes || echo NO)
  qual=$([ "${verdicts:-0}" -ge 3 ] && echo yes || echo NO)
  a=$([ "${acpt:-0}" -ge 1 ] && echo yes || echo NO)
  s=$([ "${simp:-0}" -ge 1 ] && echo yes || echo NO)

  missing=""
  [ "${verdicts:-0}" -lt 3 ] && missing+="reviews(${verdicts:-0}/3) "
  [ "$a" = NO ] && missing+="acceptance "
  [ "$s" = NO ] && missing+="simplify "
  [ -z "$missing" ] && missing="-"

  printf '%-7s %-9s %-14s %-4s %-4s %-4s %-4s %-4s  %s\n' \
    "#$n" "$short" "$mergeable" "$rev" "$deep" "$qual" "$a" "$s" "$missing"
done
