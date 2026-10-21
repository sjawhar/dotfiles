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

- Follow the repository's workflow for the kind of change (application, infrastructure, task authoring, task queueing). One kind's checklist is not proof for another. Exercise every boundary that the change crosses.
- Every artifact has a consumer. A skill or prompt: a fresh agent walks it. Config: apply it and observe the service. CI: trigger it. A doc: a person who did not read the diff follows it literally.
- Use the user's own access path, not a privileged shortcut. A minted cookie is not the SSO login.
- Use a production-like surface that has the resource the change touches: a dev stack, staging, or a local stack with real migrations.
- When the real path is hard to drive, build the test tooling. It is in scope.
- Code that runs only after merge stays untested until you run it against a dev stack yourself.
- A proof must not do by hand a step that the product owns. When it must, record the gap in Dispatch, never as a GitHub issue Sami did not ask for, and name it at the step.
- For a page or workflow: walk it as each affected role at the final head. Click every element on the changed pages and read what a user reads. Grep the e2e and spec suites for locators the change breaks, and update them.
- Fix the findings in this change. Production afterwards gets only a smoke test of the changed path.

## End-to-end scenarios

- Every change to what runs needs them, a pin, prompt, grader or workflow included. Affected means the scenarios it adds, changes or claims to fix, and every existing one that exercises what it changed. Run each at the head under review.
- In `## Verification`, record per scenario: what ran and where, the outcome, the duration, a link, and one marker:

  | Marker | Meaning |
  |---|---|
  | `RAN` | Observed through the real surface. |
  | `BLOCKED` | The failed command and its authoritative record. Not merge-ready. |
  | `WAIVED-BY-SAMI` | A named blocker that Sami explicitly waived, linked to his words. That blocker on that change only. |

- Size, kind, green CI and approvals are never waivers. Record the duration. Never skip a scenario because it is slow.
- This adds to the repository's required checks. It replaces none of them.

## Merge-ready

- The PR links the pre-merge proof. Without the link, do not arm auto-merge.
- Any unverified scenario or open grounded finding blocks readiness and merge.
- Surface not reachable: report "not merge-ready, verification blocked on X" with the failed command. Do not open the PR for review. Build the surface and run the scenario again.
- Never ask to omit acceptance. A blocker that survives that work goes to Sami as a pre-merge decision.
