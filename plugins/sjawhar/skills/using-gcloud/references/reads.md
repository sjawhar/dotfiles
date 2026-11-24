# Which identity reads what

Route 1 of `SKILL.md`, "A permission error": a read, never with a person's key, with `GCLOUD_READER_REASON` (`AZ_READER_REASON` for Azure) set to the issue key and each object read.

| Object | Read as | Command |
| --- | --- | --- |
| The organization's IAM policy and policies, the IAM policy search, Admin Activity logs, `agent_spend` rows, insights, Policy Troubleshooter and Support cases | `legion-reader@` | `gcloud-reader --as legion -- <command>` (`bq` and `sh -c` work too) |
| Azure resources | The Azure identity `legion-reader` | `az-reader <az args>` |

- Google metadata, IAM policies, a bucket's object list, metric time series, quotas and billing accounts: `gcloud-reader <gcloud args>`, as `metadata-reader@` (the default). Its roles sit on folder `theorem`, which holds the organization's projects; a project outside that folder is refused.
- The tailnet's devices (`devices`) or policy file (`acl`): agent-infra's `legion/checks/tailnet-read.sh`, run from a file, never from `<(...)`:
  `f=$(mktemp) && gh api 'repos/theorem-labs/agent-infra/contents/legion/checks/tailnet-read.sh?ref=main' -H 'Accept: application/vnd.github.raw' >"$f" && secret-run --register --ttl 15m --reason '<key>: the device list of the tailnet' TAILSCALE_READ -- bash "$f" devices; rm -f "$f"`
- Container logs of `theorem-agent-infra`, the daemons' included: the box identity, `dev-sjawhar@`, until this box moves to `dev-box@`; after that, a need is a missing grant, reported on your issue.
- A read none of these makes is a missing grant:
  - Report the call and the refusal on your issue.
  - Take no other identity.
