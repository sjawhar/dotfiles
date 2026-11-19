# Step 4: prove it works

## The mandate

Dispatch a fresh `deep` agent with this text:

```text
You did not write this code. Boot the actual artifact and use it through its real surface: TUI in
tmux, web in a real browser, API via curl, or library via a driver script against the user-facing
workflows this change is supposed to serve. Tests passing is not evidence; you have to run the
thing. Report what you ran, what you saw, and every defect.
```

## The surface

- Follow the repository's workflow for the kind of change (application, infrastructure, task authoring, task queueing). One kind's checklist is not proof for another. Exercise every boundary the change crosses.
- Every artifact has a consumer; prove it through that consumer. A skill or prompt: a fresh agent walks it. Config: apply it and observe the service. CI: trigger it. A doc: a person who did not read the diff follows it literally.
- Use the user's own access path, not a privileged shortcut: a minted cookie is not the SSO login.
- Use a production-like surface that has the resource the change touches: a dev stack, staging, or a local stack with real migrations.
- When the real path is hard to drive, build the test tooling. It is in scope.
- Code that runs only after merge stays untested until you run it against a dev stack yourself.
- A step you must run by hand that the shipped product should run itself means the proof does not cover the product. Record that gap in Dispatch, not as a GitHub issue nobody asked for, and name it at the step.
- A page or workflow: walk it as each affected role at the final head. Click every element on the changed pages and read what a user reads. Grep the e2e and spec suites for locators the change breaks, and update them.
- Fix the findings in this change. Production afterwards gets only a smoke test of the changed path.

## End-to-end scenarios

- Every change to what runs needs them, a pin, prompt, grader or workflow included. The affected scenarios are the ones the change adds, changes or claims to fix, and every existing one that exercises what it changed. Run each at the head under review.
- In `## Verification`, record per scenario what ran and where, the outcome, the duration, a link, and one marker:
  - `RAN`: observed through the real surface.
  - `BLOCKED`: the failed command and its authoritative record. Not merge-ready.
  - `WAIVED-BY-SAMI`: a named blocker that Sami explicitly waived, linked to his words. It covers that blocker on that change only.
- Size, kind, green CI and approvals are not waivers. Never skip a scenario because it is slow.
- This adds to the repository's required checks and replaces none of them.

## Merge-ready

- The PR links the pre-merge proof. Without the link, do not arm auto-merge.
- Any unverified scenario, and any open finding backed by a file, a command output or a trace, blocks readiness and merge.
- Surface not reachable: report "not merge-ready, verification blocked on X" with the failed command, do not open the PR for review, build the surface and run the scenario again.
- Do not ask Sami to drop a scenario. A blocker that survives that work goes to him as a pre-merge decision.

## Excuses that mean: run this step

- "CI is green", "the mechanism says it works", "verified by inspection": you saw a mechanism, not a result.
- "Only a pin, a prompt, a doc", "too small to boot": what runs changed. Find its consumer and run it.
- "I verified it before the last fixes": those fixes are the diff now.
- "I disclosed the gap", "no test environment here": report not merge-ready, do not open it for review, and build the missing surface.
- "It gets verified after the merge", "Sami wanted it fast", "separate PR", "follow-up issue?": that is deferral, and the change is unverified now. Do it now.
- "Want me to run the smoke test?": do not ask. Running it is the job.
- "Nothing is actionable until you merge": usually false. Read this list again.
