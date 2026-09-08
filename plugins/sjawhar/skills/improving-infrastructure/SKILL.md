---
name: improving-infrastructure
description: Use when implementing an approved root fix for infrastructure, deployment behavior, cloud permissions, or an infrastructure runtime path.
---

# Improving Infrastructure

An approved infrastructure improvement is an implementation task, not a coordination handback. Own the root cause through the target repository's delivery workflow.

## Implement the root fix

1. Read the target's IaC or deployment source, current failure evidence, existing plan, and relevant operational contracts. For Agent-C, start with `developing-infra`, `deploying-infra`, and the applicable operating skill; for another repository, use its established runbooks.
2. Trace the affected resource, deployment path, and final consumer before editing. Reuse the repository's proven mechanism rather than adding a deployment, coordination, or testing framework.
3. Make the smallest direct source or configuration change that fixes the approved root cause. Migrate every affected caller and remove any obsolete path the change supersedes.
4. Update relevant existing checks when the changed contract needs coverage. Keep their established scope and policy; do not substitute a plan, rendered output, or green CI for runtime evidence.

## Prove and deliver

5. Run the authorized preview and applicable runtime verification using the repository's existing proof path. A code change does not authorize an apply, teardown, credential change, or other production mutation; leave an ungranted action unperformed and report it precisely.
6. Complete the target repository's existing review and delivery workflow. Do not return recommendations or a plan-only handback.
7. Report the actual files changed, root-cause observation, checks or runtime results, and any verification that was unavailable under the granted authority.
