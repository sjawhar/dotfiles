---
name: using-gcloud
description: Use when running gcloud compute ssh or scp against several VMs, or where other sessions also use gcloud. Use on the ssh error "No ED25519 host key is known for compute.<id> and you have requested strict checking" or "Host key verification failed". Use when gcloud fails with "Reauthentication failed. cannot prompt during non-interactive execution." Use when you need a new build or measurement VM, give a VM a public IP, or deploy a service that people or machines will reach. Use when a docker pull or push, a gcloud call or a client library is refused with PERMISSION_DENIED, "denied", 403, or a missing artifactregistry, storage or iam permission, and before asking any person for a grant or role. Use before you run docker login, set a git credential helper, or copy, mint or reuse any token or credential that belongs to another account.
---

# Using gcloud

## Which identity gcloud uses

On an Azure VM enrolled in the box identity (`~/.dotfiles/installers/gcloud-box.sh`), gcloud acts as the service account `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com`, in the active configuration `box` (project `autoformalization`). In an agent session, client libraries and Pulumi act as it too: the session sets `GOOGLE_APPLICATION_CREDENTIALS` to its keyless configuration `~/.config/gcloud/box_credential_config.json`. It needs no sign-in and does not expire. Do not unset that variable or point it at `application_default_credentials.json`: on sami-rocqperf that file has held a person's own login since 2026-11-01.

Use the box identity for reads and for your session's own work. When a step fails as the box with a permission error, take the first route below that fits; the aim is that no step needs a person's login (dispatch://INFRA-243's spec, "Admin logins leave the boxes", which Sami approved at [dispatch://INFRA-243/ask/3dd87e3f](https://dispatch.tail12ec1c.ts.net/issues/INFRA-243/asks/3dd87e3f-f6df-4d05-b077-44c3028fb20f)):

- **A production change** goes through its program's CI route: merge it to main, and the program's apply workflow applies it. Where the program's README names an apply from a session, run it as agent-infra's applier `infra-applier@theorem-agent-infra.iam.gserviceaccount.com`, impersonated from the box identity, with the two exports of agent-infra `bootstrap/README.md`, "Who applies":
  `export CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=infra-applier@theorem-agent-infra.iam.gserviceaccount.com` and
  `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token --impersonate-service-account="$CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT")`.
  A grant that the box or the applier lacks is a change in code: widen it there, so the next run needs no person.
- **A step only a person can make** is on agent-infra's break-glass page, with that person's steps: <https://github.com/theorem-labs/agent-infra/blob/main/docs/break-glass.md>. Send that person a `dispatch ask` that names the step and cites that URL. Do not make the step yourself with any identity.

**Sami's own login** (sami@theorem.dev, gcloud configuration `theorem`) is allowed only under his standing permission ([dispatch://INFRA-6/ask/6c7b1e80](https://dispatch.tail12ec1c.ts.net/issues/INFRA-6/asks/6c7b1e80-176f-40e3-967e-e5d642d501ec)): for an apply that none of the routes above can make yet, select that configuration with `--configuration` on that one command, and record each use on dispatch://INFRA-6: what it was, when, why no declared identity could do it, and the change that removes the need. Never use it for a seed credential, a permission boundary or a new project: those are his own steps, on the break-glass page. Never select it by default: no exported `CLOUDSDK_ACTIVE_CONFIG_NAME`, `GCLOUD_ACCOUNT`, `CLOUDSDK_CORE_ACCOUNT` or `GOOGLE_APPLICATION_CREDENTIALS` that puts later commands under it. In an agent session `~/.dotfiles/shims/gcloud` refuses a call that names his account by `--account` or `CLOUDSDK_CORE_ACCOUNT` without that explicit `--configuration`, and its refusal names the box identity, the CI route, the applier and the break-glass page. Do not start `gcloud auth login`. Never change the active configuration or the ADC file: every session on the machine uses them.

**A refusal is a stop, not a prompt to borrow an identity.** Never move a person's credential to where a machine keeps it: minting a token from a person's login (`gcloud auth print-access-token` under a person's configuration) and feeding it to `docker login`, a git credential helper or a kubectl context writes that identity into a file every process on a shared host can read, and logs every later action as that person. Instead find the account that is meant to hold the access and run as it, or impersonate it (`--impersonate-service-account`). On 2026-10-31 an agent did exactly this on `linux28-agents-7` to get past a registry denial, and it had to be withdrawn.

Refused by a registry or shared environment: use an account that already has access, or copy it from a host that has it; never ask a person, the list is settled. A Google Cloud project a customer owns is theirs alone to grant, so no one at Theorem can be asked for a role on it; the private skill for that customer's platform (`~/.dotfiles/.secrets/dotfiles/skills/`) lists the accounts that already hold access.

## Which identity az uses

In an agent session `az` runs as the machine's own Azure identity, not as Sami: `~/.dotfiles/shims/az` sets `AZURE_CONFIG_DIR=~/.azure-box-identity` when the caller set none (`shims/AGENTS.md`). That identity starts, stops, restarts and reads the VMs of resource group `sami` and runs `az vm run-command invoke` on them, so no az step needs his login.

Never set `AZURE_CONFIG_DIR` to `~/.azure`, and never run `az login` there: that is Sami's own login, and a command under it is recorded as his act (dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec). Directory work (`az ad`, a role assignment by user name) is the one thing the machine identity cannot do; ask for it on Dispatch instead of borrowing his account.

## Expired login ("Reauthentication failed")

The box identity and the applier need no sign-in and do not expire, so as either you do not see this error. If you see it, the call ran as a user login, not as the box:

1. Run it again with no configuration or account selected, as the box. For an apply, impersonate the applier (above).
2. For a build or measurement machine, use `az vm create` (resource group `sami`, location `westus2`): `az` runs as the machine's own identity and needs no daily login.
3. Do not start `gcloud auth login`. If an apply that needs his login under his standing permission (above) finds it expired, post the step on the open re-login ask (`dispatch search --query 'gcloud re-login'`), or open one if none is open. A step only a person can make goes to that person as the `dispatch ask` above, citing the break-glass page.
4. Do not run `gcloud auth application-default login` or `revoke`, and never add `--update-adc` to a login. Each writes or removes `application_default_credentials.json`, which every process outside an agent session reads; in an agent session `shims/gcloud` refuses all three on every route.

## A new VM or service stays off the internet

People, dev boxes, VMs, CI jobs and managed services reach each other over the VPN once they are enrolled in it, so a public address gives such a service no access it needs and exposes it to everyone.

- Create a VM with no external address (`gcloud compute instances create … --no-address`, `az vm create … --public-ip-address ""`) and reach it over IAP (`--tunnel-through-iap`) or the VPN. The VM then needs Cloud NAT or Private Google Access for its own outbound traffic; check that the subnet has one before you create it, because a no-address VM with neither cannot reach apt, pip or a registry.
- Taking the address off a VM that has one (`gcloud compute instances delete-access-config`) cuts every connection to that address, egress included. Do it in this order: prove IAP or VPN access to the VM, prove its outbound path, then delete the access config, then check access again.
- Deploy a service that only your own people and machines call onto the VPN. Never `gcloud run deploy --allow-unauthenticated`, an `allUsers` IAM binding, an internet-facing load balancer, or a firewall rule that opens its port to `0.0.0.0/0`; a shared bearer token is not a substitute, because the port is still reachable by everyone.
- Open a service to the internet only for a named caller outside the organization that cannot join the VPN, such as a webhook sender. Name that caller and the reason where the service is documented, so the next reader can check it.

## Host keys: one gcloud ssh call at a time

Each `gcloud compute ssh` or `scp` call rewrites all of `~/.ssh/google_compute_known_hosts` with no lock. Parallel calls remove each other's keys, also for other sessions on the machine.

- Never run two such calls at the same time: no `xargs -P`, `&` or parallel loop. A serial warm-up call does not prevent the race.

## Run one command on many VMs in parallel

1. One VM at a time: `gcloud compute ssh <vm> --zone <zone> --project <project> --tunnel-through-iap --dry-run > <vm>.cmd`. This writes the host keys and saves the ssh command.
2. In parallel: `eval "$(cat <vm>.cmd) -- '<command>'" &`, then `wait`. Plain `ssh` only reads the file.

On "Permission denied (publickey)", run one real `gcloud compute ssh` to that VM to install your key, then repeat step 1.

## Strict host key checking fails

1. Run the same `gcloud compute ssh` again, alone. It writes the keys again.
2. If it fails again, compare the file's key with `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`. If they differ, stop and send the user the VM name and both keys.

Never remove `~/.ssh/google_compute_known_hosts`: it holds every session's keys. Never use `StrictHostKeyChecking=no` or `accept-new`: they stop the check that the VM is the right one.
