---
name: resolve-conflicts
description: "Use when merge conflicts exist after a rebase, merge, or branch integration, or when file moves or renames cause path-level conflicts. Also use when verifying a reconstructed PR head that has no conflict markers."
---

# Resolve Conflicts

## 1. Map both sides first

- List the conflicted files with `git status`.
- For each side, read `git diff $(git merge-base HEAD <side>)..<side> --stat`, then the full diff. `<side>` is `HEAD`, then `MERGE_HEAD` for a merge, or `REBASE_HEAD` or the incoming branch tip for a rebase.
- Keep reading until you know each side's path and content changes and where they overlap. For each overlap, know which version is better, and why. Resolve nothing before that.

## 2. Classify each conflict

- Moved or renamed on one side, changed on the other: use the right path and keep the content changes.
- Same lines changed on both sides: read both, then combine them or take the better one.
- Moved and changed differently on each side: resolve the path first, then the content.
- Deleted on one side, changed on the other: decide if the file must exist. If yes, keep the changes.
- In `--stat`, `0 insertions, 0 deletions` is a pure move. Other counts add content changes.
- Architectural disagreement or mutually exclusive approaches: explain both sides and ask the user before you resolve. Do not guess.

## 3. Resolve each file

- Delete all markers. `<<<<<<<` to `=======` is your side, `=======` to `>>>>>>>` is theirs.
- Give each file a reason: "Taking side B: it has the cache fix", not "Taking side B".
- Read the content on both sides before you call a change "superseded".

## 4. Test the composed tree, not only the marked conflicts

- Delete each path that `git diff <old-base> <new-base> --summary` marks `D`, unless the approved change deliberately restores it and you say so in the description.
- In files that both sides changed, find lost hunks that did not conflict, docs included. Taking one whole side can discard the other side's valid work.
- Trace renamed or changed interfaces to their dynamic consumers: string-keyed registries, import loaders, monkeypatch targets.
- Run the affected packages' syntax, type, lint and test commands the way CI runs them, with the same test layout. Do not run only the test next to a conflict.
- Run focused tests while you fix, and the full affected set once before you publish.
- Before you name the cause of a failure, run the same commands in the same environment on the intended base.
- Fix integration defects before you publish. For a defect the base already has, fix it, or state the remediation and get an explicit decision to exclude it. "Pre-existing" is not a resolution.

## Stop and rethink before you

- Rebase around a conflicting parent: that loses the parent's changes.
- Add a fix-up commit that reverses changes: fix the original resolution.
- Run `git reset --hard` to try again: make one deliberate fix. Resets leave no record of what you tried, and a reset of pushed commits rewrites shared history.
- Drop commits to clean up a resolution: first make sure they are unwanted, and not pushed or relied on elsewhere. Do not touch what you don't understand.
- Start a second fix after the first fell short: redo step 1. You missed something.

**Done when:** you resolved all conflicts, every command from step 4 passes, you pushed the changes, and no stray or duplicate resolution commits remain.
