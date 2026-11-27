# Which identity reads what

Route 1 of `SKILL.md`, "A permission error": a read, never with a person's key, with `GCLOUD_READER_REASON` (`AZ_READER_REASON` for Azure) set to the issue key and each object read.

- Google metadata, IAM policies, a bucket's object list, metric time series, quotas and billing accounts: `gcloud-reader <gcloud args>`, as `metadata-reader@` (the default). Its roles sit on folder `theorem`, which holds the organization's projects; a project outside that folder is refused.
- The organization's IAM policy and policies, the IAM policy search, Admin Activity logs, `agent_spend` rows, insights, Policy Troubleshooter and Support cases: `gcloud-reader --as legion -- <command>`, as `legion-reader@` (`bq` and `sh -c` work too).
- Azure resources: `az-reader <az args>`, as the Azure identity `legion-reader`.
- The tailnet's devices (`devices`) or policy file (`acl`): agent-infra's `legion/checks/tailnet-read.sh`, run from a file, never from `<(...)`; it asks the broker for `TAILSCALE_READ` itself, with `TAILNET_READ_REASON` as the reason:
  `f=$(mktemp) && gh api 'repos/theorem-labs/agent-infra/contents/legion/checks/tailnet-read.sh?ref=main' -H 'Accept: application/vnd.github.raw' >"$f" && TAILNET_READ_REASON='<key>: the device list of the tailnet' bash "$f" devices; rm -f "$f"`
- Container logs of `theorem-agent-infra`, the daemons' included: the box identity, `dev-sjawhar@`, until this box moves to `dev-box@`; after that, a need is a missing grant, reported on your issue.
- A read none of these makes is a missing grant: report the call and the refusal on your issue, and take no other identity.
