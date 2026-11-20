---
name: using-gcloud
description: Use when a gcloud, az, registry or Google client call is refused (PERMISSION_DENIED, 403, denied, "Reauthentication failed"), before you mint, copy or reuse another account's credential, when you create a VM or expose a service, or on a gcloud compute ssh host-key error.
---

# Using gcloud

## Which identity runs

- gcloud runs as the machine's own identity, configuration `box`, which needs no sign-in and does not expire; the header of `installers/gcloud-box.sh` says what it is on a Google VM and on an Azure VM.
- Leave `GOOGLE_APPLICATION_CREDENTIALS`, the active configuration and `application_default_credentials.json` as they are: every session on the machine reads them, and on an Azure box that file can hold a person's login.
- In an agent session `az` runs as the machine's Azure identity (`shims/az`, described in `shims/AGENTS.md`), which can manage and run commands on the VMs of resource group `sami`. Never point `AZURE_CONFIG_DIR` at `~/.azure` or run `az login` there: that is Sami's own login. Directory work (`az ad`, a role assignment by user name) needs a person: ask with `dispatch ask`.

## A permission error

Never move a person's credential to where a machine keeps it: a token minted from a person's login and given to `docker login`, a git credential helper or a kubectl context lands in a file every process on the host can read, and logs each later act as that person. Run as the account meant to hold the access, or impersonate it (`--impersonate-service-account`). Take the first route that fits:

1. Reads and your session's own work: the box identity, with no other configuration or account selected.
2. A production change: merge it to main, and the program's CI apply workflow applies it. A by-hand step that a program's README gives to agent-infra's applier runs as `infra-applier@theorem-agent-infra.iam.gserviceaccount.com`, impersonated from the box identity; agent-infra `monitoring/README.md`, "The applier's session, for other programs' pages", has the exports. A grant the box, a CI account or the applier lacks is a code change in agent-infra: widen it there.
3. A step only a person can make is on agent-infra `docs/break-glass.md`, with that person's steps. Send that person a `dispatch ask` that names the step and cites the page; do not make the step with any identity.
4. Sami's own login (configuration `theorem`) only for an apply none of the above can make yet: `--configuration theorem` on that one command. Never select it for later commands (`CLOUDSDK_ACTIVE_CONFIG_NAME`, `GCLOUD_ACCOUNT`, `CLOUDSDK_CORE_ACCOUNT`), and never use it for a seed credential, a permission boundary or a new project. Post no per-use note: Cloud audit logs record each use.

Refused by a registry or a shared environment: use an account that already has access, or copy the setup from a host that has it; do not ask a person for a role. A Google Cloud project a customer owns is theirs alone to grant; that customer's private skill (under `~/.dotfiles/.secrets/dotfiles/skills/`) lists the accounts that hold access.

## "Reauthentication failed"

The box identity and the applier never expire, so this error means the call ran as a user login.

1. Run it again with no configuration or account selected. For an apply, use its CI route.
2. For a build or measurement machine, use `az vm create` (resource group `sami`, location `westus2`): az runs as the machine identity.
3. Do not run `gcloud auth login`, `gcloud auth application-default login` or `revoke`, or add `--update-adc`. If an apply under Sami's login (route 4) finds it expired, post the step on the open re-login ask (`dispatch search --query 'gcloud re-login'`), or open one.

## A new VM or service

- No external address: `gcloud compute instances create ... --no-address` or `az vm create ... --public-ip-address ""`; reach it over IAP (`--tunnel-through-iap`) or the VPN. First check that the subnet has Cloud NAT or Private Google Access, or the VM cannot reach apt, pip or a registry.
- Label it `--labels owner=sjawhar,stream=<stream>`.
- Removing a VM's external address (`gcloud compute instances delete-access-config`) also cuts its egress: prove IAP or VPN access and its outbound path, then remove it, then check access again.
- Put a service that only our people and machines call on the VPN. No `gcloud run deploy --allow-unauthenticated`, `allUsers` binding, internet-facing load balancer or firewall rule open to `0.0.0.0/0`; a shared bearer token does not replace this. Open a service to the internet only for a named outside caller that cannot join the VPN, such as a webhook sender, and name that caller where the service is documented.

## Host keys: one gcloud ssh call at a time

Each `gcloud compute ssh` or `scp` rewrites all of `~/.ssh/google_compute_known_hosts` with no lock, so parallel calls (`xargs -P`, `&`, a parallel loop) delete each other's keys, other sessions' included. A serial warm-up call does not prevent this.

To run one command on many VMs:

1. One VM at a time: `gcloud compute ssh <vm> --zone <zone> --project <project> --tunnel-through-iap --dry-run > <vm>.cmd`. This writes the host key and saves the ssh command.
2. In parallel: `eval "$(cat <vm>.cmd) -- '<command>'" &`, then `wait`. Plain `ssh` only reads the file.

On "Permission denied (publickey)", run one real `gcloud compute ssh` to that VM to install your key, then repeat step 1.

On "Host key verification failed" or "No ED25519 host key is known":

1. Run the same `gcloud compute ssh` again, alone; it writes the keys again.
2. If it fails again, compare the file's key with `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`. If they differ, stop and send the user the VM name and both keys.

Never delete `~/.ssh/google_compute_known_hosts` (it holds every session's keys), and never use `StrictHostKeyChecking=no` or `accept-new`.
