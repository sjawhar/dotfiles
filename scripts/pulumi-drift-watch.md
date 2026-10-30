# pulumi-drift-watch

Reports Pulumi stacks whose `main` is declared and not applied. Previews only: it applies
nothing, refreshes nothing, reads no secret, and refuses nothing.

## The gap it measures

`main` is the declared state. The cloud is the live state. Between a merge and an apply they
differ, and until this watch nothing on the box measured the difference.

The case that caused it, on 2026-10-30:

| time | event |
| --- | --- |
| 2026-10-29 19:06Z | agent-infra #151 (`28e20536`, INFRA-154) merges, declaring six resources for a CI preview identity. Not applied. |
| 2026-10-30 00:34Z | agent-infra #958 (`904aecc3`, INFRA-430) merges, declaring the custom role `legionWorkerProjectRead` and its binding. Not applied. |
| 02:16Z | a Legion worker pod runs its own check: **9 of 12 assertions fail**, every project read 403. That is how the gap was found. |
| 02:28Z | the INFRA lead previews `bootstrap/` on main: **8 creates pending**, two of them the Legion role. |
| 02:29Z | the lead applies the two, targeted: `+ 2 created, 367 unchanged`. Six remain. |

Two costs, not one. The obvious one is the missing resource: code that says a permission exists
while the cloud refuses it, found by whoever it breaks. The second is quieter — **the next
person's preview carries a stranger's creates.** An applier told "stop unless the plan is
exactly 44" stops on a correct apply; an applier not told that applies six resources they never
read, which here would have been a federated identity letting a GitHub workflow act as a Google
service account with state-bucket and state-key access.

## What one run does

1. Puts its own private checkout (`/mnt/nvme/sami/pulumi-drift-watch/agent-infra`, blobless
   clone) on `origin/main`. No worktree anybody works in is touched, fetched or reset.
2. Runs `pulumi preview --stack <stack> --non-interactive --diff` for each configured stack.
3. Appends one sample per stack per run to `samples.jsonl` beside the checkout, whether or not
   anything is pending — a silent log cannot be told from a dead watch.
4. Sends an Envoy message **only** when a stack has a pending plan or its preview failed. A
   clean stack says nothing: a daily "nothing to report" trains its reader to skip the day it
   matters.
5. Names every pending step by URN, so the reader can tell whose change it is without running
   the preview again.

## What it deliberately does not do

- **No apply, ever.** The report goes to people who decide.
- **No `--refresh`.** A refresh takes the state lock and rewrites state; a preview takes no lock,
  so a run can never block somebody's apply.
- **No judgement.** An unapplied merge is often correct — an apply waiting on a key move, on a
  person, or on another stack. The watch says what is pending. If a pending plan is deliberate,
  say so on its issue, so the next report is read rather than skipped.

## Credentials

The box's own gcloud login, minted per run (`gcloud --configuration=theorem auth
print-access-token`). `TAILSCALE_API_KEY` is set to a value that authenticates nowhere, which is
what `bootstrap/README.md`'s targeted runs use: the Tailscale provider has to be configurable
for the program to compile, and a preview that does not refresh never calls Tailscale.

**A stack whose preview does reach Tailscale does not belong in this watch** — it needs the real
admin key, which is a human-tier broker secret and must not sit behind a timer.

## Configuration

`STACKS` at the top of the script: `[(directory, stack)]`, today `[("bootstrap", "prod")]`.
`TARGETS` is the list of Envoy session ids that hear a report — the INFRA stream lead and the
Legion controller, who asked for it at 2026-10-30T02:28Z. A session id changes when a lead's
session is replaced; a stale id makes the watch deliver to nobody, and the run reports
`no recipient took the alert` and exits non-zero rather than claiming delivery.

## Running it by hand

```sh
pulumi-drift-watch --dry-run      # preview and print what would be sent; send nothing
pulumi-drift-watch --print-alert  # send, and print the same text
pulumi-drift-watch --always       # report even a clean stack, to prove the path end to end
```

## Turning it off

```sh
systemctl --user disable --now pulumi-drift-watch.timer
```

Nothing re-arms it: `installers/pulumi-drift-watch.sh` is standalone and not sourced by
`install.sh`, and it leaves an inactive, disabled unit alone and says so.

## Proof it fires

Run at 02:35Z on 2026-10-30, after the two Legion resources had been applied:

```
PULUMI PLAN PENDING ON MAIN: bootstrap/ stack prod at theorem-labs/agent-infra a0d64d9e1
plans 6 to create (363 unchanged).
  …::gcp:iam/workloadIdentityPool:WorkloadIdentityPool::github-preview
  …::gcp:serviceaccount/account:Account::preview
  …::gcp:iam/workloadIdentityPoolProvider:WorkloadIdentityPoolProvider::github-preview
  …::gcp:serviceaccount/iAMMember:IAMMember::preview-github
  …::gcp:storage/bucketIAMMember:BucketIAMMember::state-bucket-preview
  …::gcp:kms/cryptoKeyIAMMember:CryptoKeyIAMMember::state-key-preview
```

Six, not eight: **the two applied minutes earlier are gone from the plan**, so the run proves
both that the watch fires and that it clears.
