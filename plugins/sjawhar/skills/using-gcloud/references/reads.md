# Which identity reads what

Route 1 of `SKILL.md`, "A permission error": a read, never with a person's key, with `GCLOUD_READER_REASON` (`AZ_READER_REASON` for Azure) set to the issue key and each object read.

- Google metadata, IAM policies, a bucket's object list, metric time series, quotas and billing accounts: read as `legion-reader@` (`gcloud-reader --as legion -- gcloud ...`). `metadata-reader@`'s roles sit on folder `theorem`, which holds no project until the projects move into it; once that move's apply record reads `applied:` and a read through `METADATA_READ` answers for `theorem-agent-infra`, plain `gcloud-reader <gcloud args>` is the route again.
- The organization's IAM policy and policies, the IAM policy search, Admin Activity logs, `agent_spend` rows, insights, Policy Troubleshooter and Support cases: `gcloud-reader --as legion -- <command>`, as `legion-reader@` (`bq` and `sh -c` work too).
- Azure resources: `az-reader <az args>`, as the Azure identity `legion-reader`.
- The tailnet's devices (`devices`) or policy file (`acl`): agent-infra's `legion/checks/tailnet-read.sh`, run from a file, never from `<(...)`:
  `f=$(mktemp) && gh api 'repos/theorem-labs/agent-infra/contents/legion/checks/tailnet-read.sh?ref=main' -H 'Accept: application/vnd.github.raw' >"$f" && secret-run --register --ttl 15m --reason '<key>: the device list of the tailnet' TAILSCALE_READ -- bash "$f" devices; rm -f "$f"`
- Container logs of `theorem-agent-infra`, the daemons' included: the box identity, `dev-sjawhar@`, until this box moves to `dev-box@`; after that, a need is a missing grant, reported on your issue.
- A read none of these makes is a missing grant: report the call and the refusal on your issue, and take no other identity.
