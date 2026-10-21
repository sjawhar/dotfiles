# Commits and the PR body

## Commits

- Order groups by dependency: schema or generated API, core logic, wiring, UI, tests.
- Keep groups coherent. No behavior change hidden as "cleanup", no bypassed hooks. Split an unclear scope.

## History-only rewrites (step 9)

```bash
gh pr view <PR> --json title,headRefName,baseRefName,state,commits
before_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)
# rewrite, then:
after_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)
```

The fingerprints must match, except for an intended, explained content change. Inspect with `git diff --stat <before-sha> HEAD`.

## The body

- Follow the repository's PR template. Put each required field and fact once, under the existing headings. Omit empty optional sections.
- In `trajectory-labs-pbc/agent-c`, run `uv run python .github/scripts/checklist_routing.py check --local`, follow `docs/pre-pr-checklists.md`, and add the checklist evidence and affirmation line.
- Write short paragraphs for a technical reader who missed the work. Aim for about 150 to 250 words. A small change can be shorter. Add detail only for material reviewer risks, and never pad or bury fields to meet a word target.
- No file or function names, no jargon.
- Open with the actor or task, its failure, and why it matters. Then the changed behavior, and only the mechanism that the causal claim needs.
- `## Verification`: who drove each scenario, what they saw, and the link, with the markers from proof.md. When blocked, which scenario cannot run and why. Tests, lint and CI are support, not verification.
- On behavior-preserving work, keep an intended invariant apart from a scenario you observed.
- Paste figures with the command that produced them. For long output, cite the artifact and paste the lines with the numbers. Never transcribe a number.
- Each claim about a suite, gate or library names the executing source you read, not a comment or another PR. Point a reviewer at these claims.
- Never invent a report, baseline, measurement, screenshot, run or risk.
- Show material risks, migration order, rollout and test coverage. No internal gate checklist, no unrelated caveats. Link docs only to make intent clear.
- Name each downstream surface of a producer change, per the plan-or-spec rule in SKILL.md.
- A unify or dedupe PR shows a net loss of source lines in the unified files, or says why not.
- A review verdict links its PR comment under the App identity at the current head SHA.
- A repository someone else maintains: say what changes on their production, the number on their setup, and the rollback. Put `## Verification` and any census on the tracking issue.
- Last, a cold reader can say what failed, what changed, and what observation supports it.

## User-visible changes

- Embed the render: a screenshot, a before-and-after pair, or a recording. Capture it from the session you already have open. A description or a table of the layout is not a render.
- Say what the image proves. A component in a harness does not prove the API wiring.
- Publish with the repository's screenshot skill (`pr-screenshots`) where one exists.
