---
description: "Push changes and open a PR"
---

Push my current changes and create a pull request. Follow these steps:

1. Run `jj status` to see current changes
2. Run `jj log -r @` to see the current commit description
3. Run `jj diff` to review what will be pushed

If there's no bookmark on the current change:
- Ask me what to name the branch, or suggest one based on the change description
- Push and create the bookmark in one step with `jj git push --named=<name>=@`

If there's already a bookmark:
4. Push with `jj git push`
5. Create a Pull Request using `gh pr create` with:
   - A clear title summarizing the changes
   - A description with:
     - Summary of what changed and why
     - Any testing done
     - Notes for reviewers

If there are any issues at any step, stop and report them.
