---
name: post-merge
description: "Use after a PR is merged — when the user says 'merged', 'PR is merged', 'any cleanup or follow-up?', or a merge notification arrives for work from this session. Runs the closing sweep: local pruning, PR record accuracy, tracking state, docs, deploy."
---

# Post-Merge Sweep

A PR just merged. Run the closing sweep — execute each item, don't present a list of suggestions.

1. **Confirm the merge**: verify the PR is merged and post-merge CI on the target branch is green (if still running, set up a watcher and continue with the other items).
2. **Prune local state**: fetch (`jj git fetch`), confirm the tracking bookmark was deleted, abandon empty leftover changes, remove obsolete jj workspaces created for this work.
3. **PR record**: if the title/description drifted from what actually landed, update them.
4. **Tracking state**: close or update the linked GitHub issue(s), tracking docs, and roadmap items. Plan/spec content belongs in the issue body, not file-path references.
5. **Docs**: if the change touched documented behavior, update the docs with the `updating-docs` skill (evergreen, consolidated — not appended notes).
6. **Deploy**: if the repo has a deployment step (check AGENTS.md), follow it and verify the change is live.
7. **Report**: either "nothing left" or the short list of items you just handled. Don't propose follow-ups — anything worth doing was done in steps 1-6.

The bar is "pristine clean."
