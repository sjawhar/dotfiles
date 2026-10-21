# Verifying that a merged change is live

Read this at step 3 of the post-merge sweep.

## Proof that reads the old version

| Trap | What happens |
|---|---|
| In-flight tick | When `production-apply` lands mid-tick, that tick can finish on the OLD definition. |
| Old pods | Right after the apply, pods can still run on the old runtime. |

## The deploy-lane queue

- One chain runs and one waits. A newer merge replaces the waiting chain, so your merge lands when the newest chain finishes. Find the newest run: it carries your merge.
- The queue cancels a pending run because a chain was already applying. That run could not deploy at that time, so the cancel removes nothing.

## Watcher traps

| Trap | Example |
|---|---|
| Run completes, apply does not | A chain can finish red with the apply SKIPPED. |
| Substring match | A reusable workflow puts its own name in front of each child job. In agent-c, a match on `production-apply` hits about 26 jobs. The first to succeed is a gate before the apply (`production-apply / wait-for-apply-images`), not the apply (`production-apply / production-apply`). |

## Checkouts that sessions load from

A checkout can be several merges behind main, and no deploy step runs to tell you. Read the served file back after you advance the checkout.
