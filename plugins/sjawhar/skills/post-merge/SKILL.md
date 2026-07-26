---
name: post-merge
description: "Use after a PR is merged — when the user says 'merged', 'PR is merged', 'any cleanup or follow-up?', or a merge notification arrives for work from this session. Runs the closing sweep: local pruning, tracking state, deploy, and a clear statement of remaining follow-ups."
---

# Post-Merge Sweep

A PR just merged. Run the closing sweep — execute each item, don't present a list of suggestions.

1. **Confirm the merge**: verify the PR is merged and post-merge CI on the target branch is green (if still running, set up a watcher and continue with the other items).
2. **Prune local state**: fetch (`jj git fetch`), confirm the tracking bookmark was deleted, abandon empty leftover changes, remove obsolete jj workspaces created for this work.
3. **Tracking state**: close or update the linked GitHub issue(s), tracking docs, and roadmap items. Plan/spec content belongs in the issue body, not file-path references.
4. **Deploy**: if the repo has a deployment step (check AGENTS.md), follow it and verify the change is live.
5. **Report**: the short list of items you just handled (or "nothing left"), then **clearly state any applicable follow-ups or cleanups that remain** — full sentences with context, not bare issue numbers or shorthand.

PR record accuracy and doc updates are the `post-pr` skill's job, done while the PR was open. If you find drift anyway, fix it now and note that the post-pr sweep missed it.

The bar is "pristine clean."
