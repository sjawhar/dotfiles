> **Status, 2026-11-07: `STACKS` is empty and the timer is disabled.** `bootstrap/prod` was the
> only stack and was dropped. agent-infra #1064 (2026-11-05) moved `bootstrap/`'s state into
> `root/`'s per-program bucket, `gs://theorem-agent-root-bootstrap-pulumi-state`, which
> `infra-applier@` cannot list, so every preview since returned a 403 before planning anything
> (report `20261107T090237Z-failed.txt` on sami-rocqperf). The gap this watch measures is now
> covered for `bootstrap/` by its own CI account, which applies the stack on every merge
> (agent-infra `envoy.yaml`). The machinery is kept, tested, and ready for the next stack that
> has a reader for its state and no apply-on-merge of its own; re-arm with `systemctl --user
> enable --now pulumi-drift-watch.timer` when `STACKS` gets an entry.

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
4. Sends an Envoy message **only** when a stack's pending set changes (a URN added or removed),
   when its preview starts failing, when a preview is refused for want of a state reader, or
   when a run cannot start (once per cause). A clean stack says nothing: a daily "nothing to
   report" trains its reader to skip the day it matters. The
   same pending set again says nothing either: bootstrap/prod had pending steps in all 7 samples
   from 2026-10-30 to 2026-11-02 (6, 6, 6, 6, 1, 9, 6), so pending alone is a standing fact.
5. The message is at most 600 characters and names every pending resource by its name (the 6 of
   2026-11-02 take 193 characters), what changed since the last report, and `Full report:
   <path>` last. The report (`/mnt/nvme/sami/pulumi-drift-watch/reports/<UTC>-pending.txt`,
   pruned after 7 days) has every URN and the preview's last lines.
6. With `STACKS` empty it does none of the above: it clones nothing, mints no token, prints
   that it watches nothing, and exits 0.

### The four things a report can say

| status | report file | what it means |
| --- | --- | --- |
| `PULUMI PLAN PENDING` | `<UTC>-pending.txt` | merged and declared, not live. Sent again when the URN set changes. |
| `PULUMI PREVIEW FAILED` | `<UTC>-failed.txt` | the preview ran and broke. What is pending is unknown. |
| `NO READER FOR THIS STACK'S STATE` | `<UTC>-no-state-reader.txt` | the preview was refused with a 403 on the stack's state bucket before it planned anything. **Not drift and not a broken preview**: the identity this watch previews as cannot read that state, and the only fixes are a grant or dropping the stack from `STACKS`. Its own fingerprint, so it is said once per change rather than daily, and it does not make the run exit non-zero — a red unit every day adds nothing to the one message that already said it. |
| `PULUMI DRIFT WATCH COULD NOT RUN` | `<UTC>-could-not-run.txt` | no preview ran at all. Once per cause. |


## What it deliberately does not do

- **No apply, ever.** The report goes to people who decide.
- **No `--refresh`.** A refresh takes the state lock and rewrites state; a preview takes no lock,
  so a run can never block somebody's apply.
- **No judgement.** An unapplied merge is often correct — an apply waiting on a key move, on a
  person, or on another stack. The watch says what is pending. If a pending plan is deliberate,
  say so on its issue, so the next report is read rather than skipped.

## Credentials

agent-infra's applier, `infra-applier@theorem-agent-infra.iam.gserviceaccount.com`, whose token
the box identity mints per run by impersonation (`gcloud auth print-access-token
--impersonate-service-account=infra-applier@…`; agent-infra `bootstrap/README.md`, "Who
applies"). No person's login is used. `TAILSCALE_API_KEY` is set to a value that authenticates nowhere, which is
what `bootstrap/README.md`'s targeted runs use: the Tailscale provider has to be configurable
for the program to compile, and a preview that does not refresh never calls Tailscale.

**A stack whose preview does reach Tailscale does not belong in this watch** — it needs the real
admin key, which is a human-tier broker secret and must not sit behind a timer.

## Configuration

`STACKS` at the top of the script: `[(directory, stack)]`, today `[]` — see the comment there
for why `bootstrap/prod` was dropped. A stack belongs here only when this box's identity can
read its state and nothing else applies it on merge.
`TARGET` is the Envoy role that hears a report: `notifications.role.legion-infra-controller`,
the INFRA project's Legion controller, which owns the pending applies (dispatch://INFRA-430,
comment c78927c8) and asked for the report at 2026-10-30T02:28Z with the INFRA lead. A role
survives a new controller session; the session ids written here before 2026-11-03 did not.
The INFRA lead holds no Envoy role, so its session (`FALLBACK`,
`01a13c57-d826-7242-80fe-5f0670188b11`) hears the report only when nobody holds the
controller's role. When neither takes it, the run reports `no recipient took the alert`, exits
non-zero, and the next run sends it again.

Expected volume: at most one message a day, and only on the days the set changes. Of the 7
samples to 2026-11-02, 4 were a first report or a changed count (6, 6, 6, 6, 1, 9, 6), so about
4 messages in 4 days, fewer on weeks the set stands still. What it gives up: the INFRA lead no
longer gets a copy of every report while the controller's role has a holder.

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

Run at 02:35Z on 2026-10-30, after the two Legion resources had been applied (this text is now
the report file; the message is the short form of step 5):

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
