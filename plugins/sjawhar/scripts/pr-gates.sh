#!/usr/bin/env bash
# Report which SDD gates each PR has at its CURRENT head, so a missing gate is
# found before a packet rather than by the merge queue.
#
# The six gates (see plugins/sjawhar/commands/sdd.md):
#   1 reviewer  2 thermonuclear-deep-review  3 thermonuclear-code-quality
#   4 acceptance (tester)  5 simplify record  6 queue's own gate (not ours)
#
# A gate counts only when its comment names the current head, because a verdict
# at an older head says nothing about the code that would merge. A gate counts
# GREEN only when that verdict is not a rejection: `Verdict: CHANGES` at the head
# means the gate has run and REFUSED, which is the opposite of coverage. Until
# 2026-09-30 this script counted any verdict line as coverage, so a packet was
# sent to the queue reporting three reviews present when one of them said
# CHANGES. Red verdicts are now counted and named separately.
#
# `CHANGES(n)` IS A PROMPT TO ATTRIBUTE, NOT A VERDICT. This script reads only the
# FIRST LINE of each comment, and reviewer, deep and quality all emit `Verdict:`,
# so it cannot tell which gate posted which line. A gate that posts CHANGES and
# then MERGE at the SAME head — which happens on every PR whose rounds are
# body-only, since the head never moves — leaves a red line that is already
# superseded. legion#1610 printed `yes yes yes yes yes  CHANGES(4)` with all three
# reviewers green: two reds were quality's, superseded by its MERGE 95 minutes
# later, and two were the reviewer's, superseded by its own. So `CHANGES(n)`
# means LOOK, and the script prints the command that settles it. Do not read it
# as "do not packet", and do not read its absence as "no open rejection" either:
# a rejection in a review BODY is invisible here (use ~/.dotfiles/scripts/pr-gate).
#
# This answers a different question from ~/.dotfiles/scripts/pr-gate, and the two
# are complementary rather than duplicates:
#   * this script: WHICH of the SDD gates have judged the current head.
#   * pr-gate: is the LATEST verdict green across both surfaces, and are the
#     review threads resolved. It reads review bodies as well as issue comments,
#     knows more verdict spellings (bold lines, `Verdict: clean`, BLOCKS / DOES
#     NOT BLOCK), and lists unresolved threads with their severity.
# Run both before a packet. This one cannot see review-body verdicts or threads.
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

# PRs whose red verdicts need attributing; the tail prints their command.
attribute=""

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
  # Polarity matters: a gate that ran and refused is not a gate satisfied. Legion's
  # and the thermonuclear reviewers' negative form is `Verdict: CHANGES`, a tester's
  # is `Acceptance: FAIL`, and the queue's oracle posts `Verdict: BLOCKS`. A bold
  # line (`**Verdict: ...`) is admitted, since reviewers emit both.
  vgreen=$(grep -cE '^\**Verdict:\**[[:space:]]*(MERGE|APPROVE|clean|DOES NOT BLOCK)' <<<"$at_head" || true)
  vred=$(grep -cE '^\**Verdict:\**[[:space:]]*(CHANGES|FAIL|BLOCKS|REJECT)' <<<"$at_head" || true)
  agreen=$(grep -cE '^\**Acceptance:\**[[:space:]]*(PASS)' <<<"$at_head" || true)
  ared=$(grep -cE '^\**Acceptance:\**[[:space:]]*(FAIL)' <<<"$at_head" || true)
  simp=$(grep -ci '^\**Simplify' <<<"$at_head" || true)

  # Report the GREEN verdict count in the three review columns: three green verdict
  # comments at the head means reviewer + deep + quality have all passed it. A red
  # verdict is reported in MISSING, never as coverage.
  rev=$([ "${vgreen:-0}" -ge 1 ] && echo yes || echo NO)
  deep=$([ "${vgreen:-0}" -ge 2 ] && echo yes || echo NO)
  qual=$([ "${vgreen:-0}" -ge 3 ] && echo yes || echo NO)
  a=$([ "${agreen:-0}" -ge 1 ] && echo yes || echo NO)
  s=$([ "${simp:-0}" -ge 1 ] && echo yes || echo NO)

  missing=""
  [ "${vgreen:-0}" -lt 3 ] && missing+="reviews(${vgreen:-0}/3) "
  # Named `CHANGES?(n)` with the question mark because the script cannot tell a
  # live rejection from one the same gate has already superseded at this head.
  [ "${vred:-0}" -gt 0 ] && { missing+="CHANGES?(${vred}) "; attribute+=" $n"; }
  [ "$a" = NO ] && missing+="acceptance "
  [ "${ared:-0}" -gt 0 ] && missing+="acceptFAIL(${ared}) "
  [ "$s" = NO ] && missing+="simplify "
  [ -z "$missing" ] && missing="-"

  printf '%-7s %-9s %-14s %-4s %-4s %-4s %-4s %-4s  %s\n' \
    "#$n" "$short" "$mergeable" "$rev" "$deep" "$qual" "$a" "$s" "$missing"
done

# A red at the head may already be superseded by a later verdict from the SAME
# gate, which the first-line read cannot see. Print the one command that settles
# it rather than leaving the reader to invent it: every verdict at the head in
# time order, so a CHANGES followed by that gate's own MERGE is visible as such.
# Attribution still needs the bodies, since all three reviewers say "Verdict:":
#   gh api repos/<repo>/issues/comments/<id> --jq .body | grep -oiE 'maintainability|code-quality|deep.review|this reviewer'
if [ -n "${attribute:-}" ]; then
  echo
  echo "CHANGES? means attribute before deciding. For each PR above:"
  for n in $attribute; do
    head=$(gh pr view "$n" -R "$repo" --json headRefOid --jq .headRefOid 2>/dev/null || true)
    [ -n "$head" ] || continue
    printf '  gh api repos/%s/issues/%s/comments --paginate --jq %s | sort\n' \
      "$repo" "$n" "'.[] | select(.body | test(\"${head:0:8}\")) | \"\\(.created_at) \\(.id) \\((.body | split(\"\\n\"))[0][0:90])\"'"
  done
fi
