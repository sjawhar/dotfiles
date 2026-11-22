---
name: using-gcloud
description: Use when gcloud, az, a registry or a Google client is refused (PERMISSION_DENIED, 403, denied, Reauthentication failed); before a grant request, docker login, a credential helper or using another's credential; to create or expose a VM or service or add a public IP; on gcloud ssh/scp/host-key errors.
---

# Using gcloud

## Which identity runs

- gcloud runs as the machine's own identity, configuration `box` (project `autoformalization`), which needs no sign-in and does not expire. On an Azure VM that is the service account `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com`; the header of `installers/gcloud-box.sh` says what it is on a Google VM and on an Azure VM.
- In an agent session, Google client libraries and Pulumi act as that identity too:
  - On an Azure VM the session points `GOOGLE_APPLICATION_CREDENTIALS` at the keyless configuration `~/.config/gcloud/box_credential_config.json`.
  - On a Google VM they take that identity from the metadata server.
- Leave `GOOGLE_APPLICATION_CREDENTIALS`, the active configuration and `application_default_credentials.json` as they are: every session on the machine reads them, and on an Azure box that file can hold a person's login.
- In an agent session `az` runs as the machine's Azure identity (`shims/az`, whose header says how), which can manage and run commands on the VMs of resource group `sami`.
- Never point `AZURE_CONFIG_DIR` at `~/.azure`: that is Sami's own login.
- Never run `az login` on `~/.azure`: that is Sami's own login.
- Directory work (`az ad`, a role assignment by user name) needs a person: ask with `dispatch ask`, the Dispatch command that puts a question to a person and waits for the answer (`dispatch ask --help` gives its flags).

## A permission error

- Never move a person's credential to where a machine keeps it: a token minted from a person's login and given to `docker login`, a git credential helper or a kubectl context lands in a file every process on the host can read, and logs each later act as that person.
- Run as the account meant to hold the access, or impersonate it (`--impersonate-service-account`).

Take the first route that fits:

1. **A read:** never with a person's key.
   - Set `GCLOUD_READER_REASON` (`AZ_READER_REASON` for Azure) to the issue key and each object read; the broker gives the reader's token to your session at once.
   - `references/reads.md` names the reader of each kind of object.
   - Your session's own work, and the resources your session creates and uses for its own task (a build VM, a file in a bucket), run as the box identity, with no other configuration or account selected.
   - A change to a resource that a program declares in code is a production change (route 2).
2. **A production change:** merge it to main, and the CI apply workflow of its program (the directory with its own `Pulumi.yaml` that declares the resource) applies it.
   - For a targeted, replace or refresh apply, start that workflow by hand (agent-infra, the GitHub repository `theorem-labs/agent-infra`: `bootstrap/README.md`, "Who applies by hand").
   - Run as agent-infra's applier, `infra-applier@theorem-agent-infra.iam.gserviceaccount.com`, only a by-hand step that a program's README gives to that account.
   - Impersonate the applier from the box identity for a by-hand step that a program's README gives to it: agent-infra `monitoring/README.md`, "The applier's session, for other programs' pages", has the two exports.
   - Widen in agent-infra a grant the box, a CI account or the applier lacks: it is a code change there.
3. **A step only a person can make:** it is on agent-infra `docs/break-glass.md`, with that person's steps.
   - Send that person a `dispatch ask` that names the step and cites the page.
   - Do not make the step with any identity.
4. **Sami's own login** (configuration `theorem`): only for an apply none of the above can make yet, with `--configuration theorem` on that one command.
   - Never select it for later commands (`CLOUDSDK_ACTIVE_CONFIG_NAME`, `GCLOUD_ACCOUNT`, `CLOUDSDK_CORE_ACCOUNT`).
   - Never use it for these, which are his own steps, on the break-glass page:
     - a seed credential (a program's first credential, which the program cannot mint for itself);
     - a permission boundary (an organization policy, a deny policy, or a role or binding on the organization);
     - a new project.
   - Post no per-use note: Cloud audit logs record each use.
   - In an agent session `~/.dotfiles/shims/gcloud` refuses a call that reaches his login without that explicit `--configuration`. Its refusal names routes 1 to 3.

Refused by a registry, or by an environment that a customer shares with us (its platform):

- Use an account that already has access, or copy that account's credential and configuration from a host where it works, never a person's login.
- Do not ask a person for a role.
- A Google Cloud project a customer owns is theirs alone to grant. That customer's private skill (under `~/.dotfiles/.secrets/dotfiles/skills/`) lists the accounts that hold access.

## "Reauthentication failed"

**Read `references/reauthentication.md` now** on "Reauthentication failed".

## A new VM or service

- **No external address:** `gcloud compute instances create ... --no-address` or `az vm create ... --public-ip-address ""`.
  - Reach it over IAP (`--tunnel-through-iap`) or the VPN. The VPN is Theorem's Tailscale network, the tailnet. The section "The tailnet" of agent-infra `root/README.md` says how a VM or a service joins the tailnet.
  - First check that the subnet has Cloud NAT or Private Google Access, or the VM cannot reach apt, pip or a registry.
- Label it with its owner and its work stream: `--labels owner=<person>,stream=<stream>`, where `<stream>` is the work stream that the VM's cost counts to, such as `rocq-speed` or `oss-verify`.
  - Sami's agents use `owner=sjawhar`.
  - An organization policy in projects `autoformalization` and `theorem-research` refuses a VM create with no `owner` label.
- To remove a VM's external address (`gcloud compute instances delete-access-config`), which also cuts its egress:
  1. **Prove access.** Prove IAP or VPN access and its outbound path.
  2. **Remove the address.** Remove the external address.
  3. **Check again.** Check access again.
- Put a service that only our people and machines call on the VPN.
  - No `gcloud run deploy --allow-unauthenticated`, `allUsers` binding, internet-facing load balancer or firewall rule open to `0.0.0.0/0`.
  - A shared bearer token does not replace this.
- Open a service to the internet only for a named outside caller that cannot join the VPN, such as a webhook sender.
  - Name that caller and the reason where the service is documented.

## A VM you create gets a run-time limit

A VM left running bills until someone stops it.

- Give every VM you create by hand a limit at creation: `--max-run-duration=<duration> --instance-termination-action=STOP`.
- For such a VM with local SSD, also add `--discard-local-ssds-at-termination-timestamp=true`.

`<duration>` is the time the VM's work needs, from `30s` to `120d`, such as `8h`. Google stops the VM when the time runs out and keeps its disk. In an agent session `~/.dotfiles/shims/gcloud` refuses an `instances create` without a limit.

- To extend, start the VM again after it stops: the clock restarts at each start. `gcloud compute instances set-scheduling` changes the limit only while the VM is stopped.
- Declare a long-lived VM (a service, a dev box) in code with an `owner=` label instead of creating it by hand.

## Host keys: one gcloud ssh call at a time

Read `references/ssh-host-keys.md` before you run `gcloud compute ssh` or `scp`, and on "Host key verification failed", "No ED25519 host key is known" or "Permission denied (publickey)": parallel calls delete each other's host keys.
