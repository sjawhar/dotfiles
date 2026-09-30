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
  #
  # The head is matched by PREFIX, not by its full 40 characters. Verdict lines in
  # practice name the short head ("Verdict: MERGE at b9891193"), so a `grep -F` of
  # the full sha finds nothing and the script reports a fully gated PR as 0/3. That
  # is what it did on 2026-09-30 for legion#1612, which had three MERGE verdicts and
  # an acceptance PASS at its head. An instrument whose blind spot is undocumented is
  # worse than no instrument: this one said "missing" about work that was done.
  #
  # Any hex run of 7+ characters on the line counts when the head starts with it, so
  # both "b9891193" and the full sha match. Two limits remain, deliberately:
  #   * it cannot tell three reviewers apart from one reviewer posting three verdict
  #     comments at the same head (the coordinator posts on behalf of read-only lanes,
  #     so author identity does not separate them either);
  #   * a verdict naming an ANCESTOR of the head reads as absent, which is correct.
  at_head=$(awk -v head="$head" '
    {
      s = $0
      while (match(s, /[0-9a-f]{7,40}/)) {
        tok = substr(s, RSTART, RLENGTH)
        if (index(head, tok) == 1) { print; next }
        s = substr(s, RSTART + RLENGTH)
      }
    }' <<<"$bodies")
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
