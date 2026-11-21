# Review evidence when the head changes

Read when a verdict, packet, run or branch tip names a different SHA. This never authorizes you to rewrite an owner's branch or review its hunks. Use `using-jj` before version-control operations, in a workspace you own.

## 1. Identify both commits

- A missing body SHA needs clarification, not a comparison against an empty string.
- The PR's head association and the head repository's branch ref can briefly disagree. Pin the intended remote head. Local `HEAD`, a short log, push time and the title do not show what the PR contains.
- Ask the graph. The intersection returns the verdict SHA only for an ancestor:

```bash
jj log --no-graph -r '<verdict-sha>' -T 'commit_id ++ "\n"'
jj log --no-graph -r '<current-head>' -T 'commit_id ++ "\n"'
jj log --no-graph -r '<verdict-sha> & ::<current-head>' -T 'commit_id ++ "\n"'
```

- Resolve both inputs first. An absent object or a failed command is not a negative result.
- In another owner's store, add `--ignore-working-copy` so your read does not snapshot their files. It cannot show work they have not snapshotted.

## 2. Classify the history shape

- When an old verdict carries to a new head is in `landing-a-pr`'s review-and-readiness reference ("Review gates tied to a head"): a forward merge of the reviewed head, or a comparison of the PR's own diffs (section 3). The queue adds:
- A forward merge of main with no conflict keeps the approval: it changes none of the PR's own lines, and a PR need not contain the latest main. A merge that resolved a conflict is an integration update.
- Appended commits keep ancestry, but the delta is not proven harmless. A round the owner documents as prose-only keeps the verdict and the thermonuclear pair when the diff between the two heads touches only comments and docs; check that diff with section 3. A code change needs review at the resulting code head.
- An integration update keeps prior review only if its conflicts were prose-only and the PR's own code contribution is unchanged.
- After an amend or rebase the reviewed SHA is not an ancestor of the new head, and no comparison changes that: do not carry the old review forward as if commits were appended. An identical clean rebase keeps the old review only through an explicit confirmation that names the new head. That confirmation rests on section 3 and needs no new pair run. A changed contribution, or a conflict resolution that changes code, needs an independent re-review.
- Every shape still needs current required CI and the changed-path acceptance at the intended head, unless a scoped `WAIVED-BY-SAMI` covers it. None of this licenses an unnecessary rebase.
- A three-dot sibling comparison shows the contribution since the common base, not the amendment. A direct tree diff shows content differences, not that anyone reviewed the new tree.
- Return unexplained differences to the owner or reviewer.

## 3. Compare the PR's own contribution

- Record four reachable endpoints with commit lists: old base, actual pre-update tip, new base, current tip. A reviewer's last-seen head can omit a later commit. For a stacked PR the base is its intended published parent or fork point, not trunk.
- An old commit that exists only locally: the owner makes it reachable by an authorized ref, or that part of the proof is unverifiable. An unreachable side never counts as identical.

```bash
jj diff --git --from <old-base> --to <old-tip>
jj diff --git --from <new-base> --to <new-tip>
```

- Prefer the repository's reproducible comparison tool: it names endpoints and keeps file boundaries and failed commands visible.
- Blob equality counts only if the surrounding file did not move. An old-tip/new-tip diff includes base movement, so whole-file inequality after a clean rebase is not by itself a PR change. A count or an owner's "identical" is not proof.
- Explain file-set growth and shrinkage. A removed contribution may already be on main: read that content there. Running the comparison yourself is not a code-review judgment.
- After any approved head move, re-read remote files and commits against the packet. A reparented stale tree can look like reverts of later main changes and still pass CI. Compare the PR's file and commit counts with the packet and explain each difference; do not treat it as proof that a rebase destroyed content.

## 4. Record the reuse

- Record the old reviewed head, the current head, the graph result, the comparison artifact, the acceptance rerun and the independent confirmation.
- Never say a verdict examined a tree it did not see. If a stale verdict already merged a PR, fix the record and open a new PR for the needed code fixes, linked from the merged PR.
