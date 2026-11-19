---
name: resolve-conflicts
description: "Use when a rebase, merge or branch integration leaves conflicts, before you merge main into a branch, when moves or renames conflict, or when you check a rebuilt PR head that has no conflict markers."
---

# Resolve conflicts

The commands are git's. In a jj checkout, jj records conflicts in the commits and `using-jj` has its commands and markers; the checks in steps 1, 2 and 4 still apply.

1. Map both sides before you resolve anything.
   - List the conflicted files: `git status`.
   - Set `B=$(git merge-base HEAD <other>)`, where `<other>` is `MERGE_HEAD` in a merge and `REBASE_HEAD` (or the incoming tip) in a rebase. Read `git diff $B..HEAD --stat` and `git diff $B..<other> --stat`, then both full diffs.
   - For each overlap, be able to say which version is better and why. A resolution made before that quietly deletes work.
2. Classify each conflict.
   - Moved or renamed on one side, changed on the other: use the new path and keep the content changes. In `--stat`, a renamed file with no insertions or deletions was only moved; other counts mean its content changed too.
   - Same lines changed on both sides: combine them, or take the better one.
   - Moved and changed differently on each side: resolve the path first, then the content.
   - Deleted on one side, changed on the other: decide whether the file must exist; if it must, keep the changes.
   - Mutually exclusive designs: explain both sides and ask the user.
3. Resolve each file.
   - Remove every marker. In a merge, `<<<<<<<` to `=======` is your side and the rest is theirs; in a rebase the halves swap.
   - Give each file a one-line reason ("took `MERGE_HEAD`: it has the cache fix"), so a reviewer need not redo step 1.
4. Check the merged tree, not only the conflicted files.
   - Each path that `git diff --name-status $B <target-branch-tip>` marks `D` stays deleted, unless the change means to restore it and its description says so.
   - In files both sides changed, find hunks git merged without a conflict that your resolution dropped, docs included: taking one whole side discards the other's work.
   - Trace renamed or changed interfaces to consumers no compiler sees: string-keyed registries, import loaders, monkeypatch targets.
   - Run focused tests while you fix. Before you publish, run the affected packages' syntax, type, lint and test commands as CI runs them, with the same test layout.
   - Before you name a failure's cause, run the same commands in the same environment on the base: the base's own failure is not yours.
   - Fix every failure before you publish, the base's own included, or state the fix it needs and get the user's explicit approval to leave it out. "Pre-existing" is not a resolution.
5. Done when no conflict is left, every check in step 4 passes, the result is pushed, and `git log <base>..HEAD` has no commit that only undoes or repeats another.

## Stop and rethink before you

- Rebase around a conflicting parent: that loses the parent's changes.
- Add a fix-up commit that reverses changes: fix the original resolution instead.
- Run `git reset --hard` to try again: it leaves no record of what you tried, and a reset of pushed commits rewrites shared history.
- Drop commits to clean up a resolution: first check nobody relies on them and `git branch -r --contains <sha>` is empty.
- Start a second fix after the first fell short: redo step 1.
