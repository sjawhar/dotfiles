---
description: "Resolve merge conflicts after rebase and update PR"
---

There are merge conflicts on the current branch after rebasing onto main. Resolve them and update the PR.

Steps:
1. Run `jj git fetch` to ensure you have the latest remote state
2. Run `jj status` to see the current state and identify conflicted files
3. Run `jj log -r @` to understand the current commit and its relationship to main
4. For each conflicted file:
   - Read the file to see the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
   - Understand both sides of the conflict
   - Resolve by keeping the correct changes (usually combining our changes with upstream updates)
   - Ensure the resolved code is syntactically correct and logically sound
5. After resolving all conflicts, run `jj status` to confirm no conflicts remain
6. Run the project's quality checks (type checking, linting, tests) to verify the resolution
7. If checks pass, push with `jj git push`
8. The PR will auto-update; report the result

If conflicts are complex or ambiguous, explain both sides and ask for guidance before resolving.
