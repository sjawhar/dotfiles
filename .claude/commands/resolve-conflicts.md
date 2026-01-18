---
description: "Resolve merge conflicts after rebase and update PR"
---

There are merge conflicts on the current branch after rebasing onto main. Resolve them and update the PR.

**1. Ensure latest state:**
```bash
jj git fetch
jj status
```

**2. Understand the situation:**
- Run `jj log -r @` to see the current commit and its relationship to main
- Identify conflicted files (marked with "Conflict" in status)

**3. For each conflicted file:**
- Read the file to see conflict markers (jj uses `<<<<<<<` with sections for each side)
- Understand what each side changed and why
- Resolution strategy:
  - **Different parts of file:** keep both changes
  - **Overlapping changes:** combine logically, preserving intent of both
  - **True conflict (mutually exclusive):** prefer our changes unless upstream clearly fixes a bug
- Ensure resolved code is syntactically correct and logically sound

**4. Verify resolution:**
- Run `jj status` to confirm no conflicts remain
- Run the project's quality checks (type checking, linting, tests)

**5. Handle check failures:**
- If failure is related to conflict resolution, fix and re-verify
- If failure is unrelated to conflicts, report it as a separate issue

**6. Push and report:**
- Check if a PR exists: `jj bookmark list` and `gh pr view 2>/dev/null`
- Push with `jj git push`
- If PR exists, note that it will auto-update
- Report the resolution result

**Done when:** All conflicts resolved, checks pass, and changes are pushed.

If conflicts are complex or ambiguous, explain both sides and ask for guidance before resolving.
