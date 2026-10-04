# Review evidence when the head changes

Read this when a verdict, packet, run or branch tip names a different SHA. This is
an evidence check, not authorization to rewrite an owner's branch or review its
hunks. Use `using-jj` before version-control operations and a workspace you own.

## 1. Identify the commits before comparing them

Read the SHA the review body says it examined. `commit_id` can be the head when the
review was posted, including a new head pushed while the reviewer was writing.
A missing body SHA needs clarification, not a comparison against an empty string.
When body and metadata disagree, the body is the reviewed-head claim to verify.

Read the current PR association and the actual head-repository branch ref. They
can temporarily disagree. Pin the intended remote head; local `HEAD`, a short log,
push time and the title do not answer what the remote PR contains.

Resolve both commits, then ask the graph explicitly:

```bash
jj log --no-graph -r '<verdict-sha>' -T 'commit_id ++ "\n"'
jj log --no-graph -r '<current-head>' -T 'commit_id ++ "\n"'
jj log --no-graph -r '<verdict-sha> & ::<current-head>' \
  -T 'commit_id ++ "\n"'
```

The intersection returns the verdict SHA only when it is an ancestor. Resolve the
inputs first: an absent object or command failure is not a negative graph result.
Use `--ignore-working-copy` when inspecting another owner's store so the read does
not snapshot their files. It is not a way to inspect unsnapshotted on-disk work.

## 2. Separate the two history shapes

**Ancestor-preserving update.** Appended commits and a forward merge of main can
retain the reviewed commit as an ancestor. That makes an appended-delta question
well-defined; it does not prove the delta harmless. A documented minor-only prose
round can retain its verdict without a fresh pair or verdict. Verify its stated
scope mechanically; return an unexplained difference to the owner/reviewer instead
of judging hunks. Real code changes need review at the resulting code head.

For an integration update that retains ancestry, the prior review can be reused
when conflicts were prose-only, the PR's own code contribution is unchanged and
its behavior proof has been re-executed at the new head. Text resolution alone
cannot establish preserved behavior under the new base.

**Rewritten history.** An amend or rebase can make the old reviewed SHA a sibling,
not an ancestor. Stop append-only carry. No comparison can make ancestry survive
a rewrite. A three-dot sibling comparison answers contribution since the common
base, not “what was amended”; a direct tree diff answers content differences but
still cannot prove the new tree was reviewed.

For an identical clean rebase, reuse the old review work only through an explicit
confirmation naming the new head, backed by the own-contribution comparison below.
That is a confirmation, not automatically a new thermonuclear campaign. If the
contribution changed, the owner obtains the appropriate independent re-review.
Conflict resolutions that change code are new content, not a clean rebase.

Both shapes still require current required CI and the actual changed-path
acceptance at the intended head. An earlier green CI or proof is not a substitute
for rerunning the required scenario; only an explicitly scoped `WAIVED-BY-SAMI`
blocker is the named exception. This check does not license unnecessary rebases.

## 3. Compare the PR's own contribution, not whole trees across bases

Record all four reachable endpoints and their commit lists:

- old base and actual pre-update tip;
- new base and current tip.

For a stacked PR, the base is its intended published parent/fork point, not trunk
chosen by convenience. The actual pre-update tip matters: a reviewer's last-seen
head can omit a later commit. If a needed old commit is only local, the owner must
make it reachable by an authorized reference or label that part of the proof
unverifiable. An unreachable left side never counts as identical.

Compare the two own-contribution patches, retaining file boundaries:

```bash
jj diff --git --from <old-base> --to <old-tip>
jj diff --git --from <new-base> --to <new-tip>
```

Use the repository's existing reproducible comparison tool where present. It must
identify endpoints, show successful nonempty reads and report differences without
silently dropping file boundaries or failing commands. Blob equality is useful
only when the surrounding file did not move. A direct old-tip/new-tip tree diff
includes base movement; whole-file inequality after a clean rebase is not by itself
a PR change. A mere count or an owner's “identical” assertion is not proof.

Explain file-set growth and shrinkage alike. A removed contribution may already
be on main; verify that content instead of treating every count change as a defect.
Unexplained changes return to the owner/reviewer. The controller can run the
comparison and verify its result without making a code-review judgment.

After any approved head move, re-read remote files and commits against the packet.
Reparenting a stale whole tree can look like deliberate reverts of intervening
main changes and pass ordinary CI. Scope arithmetic is a reason to investigate,
not proof that a particular rebase command destroyed content.

## 4. Record exactly what was reused

Record the old reviewed head, current confirmed head, graph result, comparison
artifact, acceptance rerun and independent confirmation. Do not say the original
verdict named a tree it never examined. If a stale verdict was already used to
merge, correct the record and route necessary fixes through a follow-up; never
retroactively claim the original verdict covered the new code.
