---
name: improving-e2e-tests
description: Use when improving end-to-end regression coverage, test reliability, or execution speed in an existing repository harness.
---

# Improving End-to-End Tests

Own the test improvement through a concrete change and verification on the target repository's real harness.

## Improve the real path

1. Read the selected harness, its current policy, the reported evidence, and the target repository's testing workflow. For Agent-C, use `smoke-testing`, `running-evals`, `using-hawk`, and the actual harness or eval-set source; otherwise use the project's established equivalents.
2. For a coverage gap, reproduce the reported failure on that harness before editing. Add a regression at the observable path that fails for the original defect and retains the harness's real assertions.
3. For speed or flakiness, measure and profile the same case population before changing it. Identify an actual fixture, setup, or test bottleneck; fix that cause, then compare the same population's cold and warm results, including timings and failures.
4. Preserve assertions, coverage, and the existing advisory or gating policy unless an explicit approval changes it. Do not replace the real driver with a proxy or create a new test framework.

## Verify and deliver

5. Run the changed regression and the applicable existing smoke or evaluation path. Report only observations from the actual harness, not recommendations or test inspection.
6. Finish through the repository's existing review and delivery workflow. Deliver the source and test changes with concrete reproduction, measurement, and verification results.
