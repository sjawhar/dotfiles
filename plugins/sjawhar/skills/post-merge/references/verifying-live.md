# Verifying that a merged change is live

Read this at step 3 of the post-merge sweep.

## Proof that reads the old version

| Trap | What happens |
|---|---|
| In-flight scheduled run | When the deploy's apply job (for example `production-apply`) finishes while a scheduled run of the service is in progress, that run can finish on the OLD definition. |
| Old pods | Right after the apply, pods can still run on the old runtime. |

## The deploy-lane queue

- The deploy workflow's queue holds one running run and one pending run. A newer merge replaces the pending one, so your merge deploys when the newest run finishes. Find the newest run: it carries your merge.
- The queue cancels a pending run that a newer run replaced while another run was applying. The cancelled run had deployed nothing, and the newest run carries its merges too, so the cancel loses nothing.

## Watcher traps

| Trap | Example |
|---|---|
| Run completes, apply does not | A deploy run can finish red with the apply SKIPPED. |
| Substring match | A reusable workflow puts its own name in front of each child job. In agent-c, a match on `production-apply` hits about 26 jobs. The first to succeed is a gate before the apply (`production-apply / wait-for-apply-images`), not the apply (`production-apply / production-apply`). |

## Checkouts that sessions load from

A checkout can be several merges behind main, and no deploy step runs to tell you. Read the served file back after you advance the checkout.
