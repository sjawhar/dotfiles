# Step 8: commits and the PR body

## Commits

- Order groups by dependency: schema or generated API, core logic, wiring, UI, tests.
- Keep groups coherent: no behavior change hidden as "cleanup", no bypassed hooks. When one commit message cannot state a group's purpose in one line, split the group until each can.
- Pass a commit message or PR body as a file, never as a double-quoted shell argument: `git commit -F -`, `gh pr create --body-file`, or a single-quoted heredoc (`<<'EOF'`). Inside double quotes a backtick or `$(…)` is command substitution, so prose that quotes a command runs it and its output lands in the message.

## History-only rewrites

Before you reorder, split or squash commits, fingerprint the PR's diff, and compare after:

```bash
gh pr view <PR> --json title,headRefName,baseRefName,state,commits
before_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)
# rewrite, then:
after_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)
```

The fingerprints must match, except for an intended, explained content change: a reorder or squash can drop a hunk silently. Inspect with `git diff --stat <before-sha> HEAD`.

## The body

This section is for our own repositories. An upstream PR body follows "The upstream PR description" in [fork-and-upstream.md](fork-and-upstream.md) instead.

- Follow the repository's PR template. Put each required field and fact once, under the existing headings. Omit empty optional sections.
- Where the repository has `.github/scripts/checklist_routing.py`, run `uv run python .github/scripts/checklist_routing.py check --local`, follow the repository's `docs/pre-pr-checklists.md`, and add the checklist evidence and affirmation line.
- Write short paragraphs for a technical reader who missed the work: about 150 to 250 words, shorter for a change of one concern (SKILL.md step 8). Add detail only for a material risk: one that could change whether or how a reviewer approves, such as a behavior change existing users see, a step that cannot be rolled back, or a security, cost or performance change. Never pad or bury fields to meet a word target.
- No file, function or class names, and no term a reader outside this repository would have to look up: name the actor and what it does instead.
- Open with the actor or task, its failure, and why it matters. Then the changed behavior, and only the mechanism the causal claim needs.
- `## Verification`: who drove each scenario, what they saw, and the link, with the markers from [proof.md](proof.md). When blocked, which scenario cannot run and why. Tests, lint and CI are support, not verification.
- On behavior-preserving work, list what you intended to keep unchanged and what you actually ran under separate headings, so a reviewer cannot read an intention as an observation.
- Paste figures with the command that produced them. For long output, cite the artifact and paste the lines with the numbers. Never transcribe a number.
- Each claim about a suite, gate or library cites the `path:line` of the executing source you read, not a comment or another PR.
- Never invent a report, baseline, measurement, screenshot, run or risk.
- Show material risks, migration order, rollout and test coverage. Leave out internal gate checklists, except the checklist evidence and affirmation line that `checklist_routing.py` asks for (above), and any caveat that is not a material risk. Link docs only to make intent clear.
- Name each downstream surface of a producer change, as the census rule in SKILL.md says.
- A unify or dedupe PR shows a net loss of source lines in the unified files, or says why not.
- A review verdict links the review comment posted at the current head SHA by a GitHub App, whose author login ends in `[bot]`, not by a human account: the repository's review App where it has one (`landing-a-pr`'s `repo-merge-rules.md`), else the App identity your `gh` posts as.
- A repository someone else maintains: say what changes on their production, the benefit you measured on their setup (the figure and the command that produced it), and the rollback. Put `## Verification` and any census on the issue that already tracks this work, such as its Dispatch issue (open no new issue for them), and link it from the body in one line.
- Last, give only the body, without the diff, to a fresh agent and ask it what failed, what changed, and which observation supports that. Add each part it cannot answer from the body.

## User-visible changes

- Embed the render: a screenshot, a before-and-after pair, or a recording, captured from the session you already have open. A description or a table of the layout is not a render.
- Say what the image proves. A component in a harness does not prove the API wiring.
- Publish with the repository's screenshot skill (`pr-screenshots`) where one exists.
