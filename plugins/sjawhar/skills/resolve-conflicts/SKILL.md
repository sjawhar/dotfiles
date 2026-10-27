---
name: resolve-conflicts
description: "Use when merge conflicts exist after a rebase, merge, or branch integration, or before you merge main into a branch. Also when file moves or renames cause path-level conflicts, and when verifying a reconstructed PR head that has no conflict markers."
---

# Resolve conflicts

## 1. Map both sides first

- List the conflicted files with `git status`.
- Set `B=$(git merge-base HEAD <other>)`, where `<other>` is `MERGE_HEAD` for a merge or `REBASE_HEAD` (or the incoming tip) for a rebase. Read `git diff $B..HEAD --stat` and `git diff $B..<other> --stat`, then both full diffs.
- Resolve nothing until you know both sides' path and content changes and can say, for each overlap, which version is better and why. Resolving before that quietly deletes work.

## 2. Classify each conflict

- Moved or renamed on one side, changed on the other: use the right path and keep the content changes.
- Same lines changed on both sides: read both, then combine them or take the better one.
- Moved and changed differently on each side: resolve the path first, then the content.
- Deleted on one side, changed on the other: decide if the file must exist. If yes, keep the changes.
- In `--stat`, `0 insertions, 0 deletions` is a pure move. Other counts add content changes.
- Architectural disagreement or mutually exclusive approaches: explain both sides and ask the user. A guess lands an architecture nobody chose.

## 3. Resolve each file

- Delete all markers. Merging, `<<<<<<<` to `=======` is your side and the rest theirs; rebasing, the halves swap.
- Give each file a reason: "taking `MERGE_HEAD`: it has the cache fix", so a reviewer need not redo step 1.

## 4. Test the merged tree, not only the conflicted files

- Delete each path that `git diff --name-status <merge-base> <target-branch-tip>` marks `D`, unless the approved change deliberately restores it and you say so in the description.
- In files that both sides changed, find hunks git merged without a conflict and your resolution dropped, docs included: taking one whole side discards the other's valid work.
- Trace renamed or changed interfaces to consumers no compiler sees: string-keyed registries, import loaders, monkeypatch targets.
- Run focused tests while you fix, and before you publish run the affected packages' syntax, type, lint and test commands as CI runs them, same test layout. The test next to a conflict passes while the integration is broken.
- Before you name a failure's cause, run the same commands in the same environment on the intended base: the base's own failure is not yours.
- Fix every failure before you publish, the base's own included, or state the fix it needs and get the user's explicit approval to leave it out. "Pre-existing" is not a resolution.

## Stop and rethink before you

- Rebase around a conflicting parent: that loses the parent's changes.
- Add a fix-up commit that reverses changes: fix the original resolution.
- Run `git reset --hard` to try again: resets leave no record of what you tried, and a reset of pushed commits rewrites shared history. Make one deliberate fix.
- Drop commits to clean up a resolution: make sure nobody wants or relies on them and `git branch -r --contains <sha>` is empty.
- Start a second fix after the first fell short: redo step 1. You missed something.

**Done when:** you resolved all conflicts, every command from step 4 passes, you pushed, and `git log <base>..HEAD` has no commit that only undoes or repeats another.
