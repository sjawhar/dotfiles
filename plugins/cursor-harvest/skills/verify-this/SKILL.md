---
name: verify-this
description: "Verify a claim with fresh local evidence: restate it falsifiably, capture baseline and treatment, compare artifacts, and return VERIFIED, NOT VERIFIED, or INCONCLUSIVE."
---

# Verify This

Verification is not a recap. It proves or disproves a specific claim with repeatable evidence.

## When To Use

- The user asks "verify this", "prove it works", "did this fix it", or "show me the evidence".
- A bug fix needs a before/after repro.
- A UI, CLI, API, performance, or memory claim needs measurement.
- A test passes but the user-visible behavior still needs confirmation.

Do not use this for vague claims like "the code is cleaner". Ask for a measurable claim first.

## Workflow

1. Restate the claim in falsifiable form: condition, metric, and threshold.
2. Pick the smallest local surface that can disprove it.
3. Capture a baseline from the old state: merge base, parent commit, failing branch, or current broken repro.
4. Capture treatment from the changed state with the same command, data, warmup, and environment.
5. Compare raw artifacts: numbers, screenshots, terminal transcripts, HTTP responses, profiles, heap snapshots, or test output.
6. Return exactly one verdict: `VERIFIED`, `NOT VERIFIED`, or `INCONCLUSIVE`.

Before trusting any measurement, name two scopes: what the measurement covers and what the claim covers. Where they differ, the number is true about something else — `pytest -k <the tests you wrote>` measures your tests while the claim is about the suite the gate runs; a sweep over the files you already know measures your list, not the tree; mutations on a component measure the component, not the caller that decides to use it. The gap, not carefulness, is what separates a green measurement from a red reality. Inferred from the 21-instance measurement family in agent-c `docs/solutions/2026-09-18-an-instrument-is-only-trustworthy-against-the-real-input.md` (17 instances 2026-09-17/18 across eight lanes; 4 more 2026-09-19, SRE lane, three caught only by reviewers).

For a Dispatch record, a root-cause verdict carries the reproducing command or test in the same message. Without that evidence, write `hypothesis` rather than a causal conclusion; a diagnosis in progress is still useful when labelled as such. This is the Dispatch-record form of the baseline/treatment comparison, not a requirement to withhold an investigation update. Inferred from the astro lane's 2026-09-16 retro (platform PO, 2026-09-17).

## Local Surfaces

- Code behavior: focused unit/integration tests or a minimal repro script.
- CLI/TUI behavior: `control-cli`, terminal transcript, or demo recording.
- UI behavior: `control-ui`, screenshots, accessibility snapshots, or browser traces.
- API behavior: local HTTP/RPC request and response diff.
- Performance: same-machine baseline/treatment timings or CPU profiles.
- Memory: heap snapshots before and after the suspected operation.

## Artifact Layout

When safe to write artifacts:

```text
/tmp/verify-this/<claim-slug>/
├── claim.md
├── timeline.md
├── baseline/
├── treatment/
├── diff/
└── verdict.md
```

If artifacts may contain sensitive code, prompts, screenshots, HTTP bodies, or heap data, keep only the minimal inline evidence unless the user agrees to disk storage.

## Verdict Rules

- `VERIFIED`: baseline and treatment differ in the predicted direction, by the claimed threshold, with no obvious confound.
- `NOT VERIFIED`: the behavior is unchanged, moves the wrong way, or misses the threshold.
- `INCONCLUSIVE`: no valid baseline, noisy signal, failed measurement, or an environment difference invalidates the comparison.

## Output

Use this shape:

```text
VERIFIED | NOT VERIFIED | INCONCLUSIVE
Claim: <falsifiable claim>

Evidence:
<metric/artifact>: baseline=<...>, treatment=<...>, delta=<...>, threshold=<...>

Reasoning:
<one tight paragraph naming the evidence and any confounds>
```

Do not soften a negative result. A clear `NOT VERIFIED` is useful.
