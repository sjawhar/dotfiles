# One laptop daemon over the tailnet

## Prerequisites and the laptop command

Release Forward with multi-peer daemon support before running this installer.
The release must accept `peers = ["<literal IP>", ...]`, retain single `peer`
configs for the existing pairs, and automatically allow each peer's preview
endpoint at `<IP>:12802`.

Repin `tools.forward` in `mise.toml` to that release and distribute this dotfiles
revision to the laptop and each VM first. The installer installs the selected
Forward version if it is missing; it does not upgrade an already selected binary.
Do not move the separate `tools.secrets` pin or restart secretsd for this migration.
Python 3.11 or newer, a joined Tailscale client, and a working systemd user session
are required on each machine. Forward uses `~/.config/forward/config.toml` for
the shell, user units and agent boxes. `XDG_CONFIG_HOME` must be unset, empty or
exactly `$HOME/.config`; installers and box channel discovery reject other
values. The installer checks this before writing files or changing services.
Both Forward user units also pass this config path explicitly, so a different
`XDG_CONFIG_HOME` inherited by the user manager cannot select another config.
No laptop access or service changes occur merely from committing these files.

After those prerequisites, Sami runs exactly one command on the laptop:

```bash
bash ~/.dotfiles/installers/forward.sh daemon
```

This is migration step 2, not a dry run. It generates one daemon config, disables
and stops legacy pair instances, and enables and restarts the single daemon and
browser relay. It retains the old per-pair configs, template units, keys and SSH
configuration.
The laptop channels become available to each VM when that VM completes step 3;
coordinate steps 2 and 3 as one migration window.

## Migration

1. Release forward with `peers`. A single `peer` stays valid, so old pairs keep working.

   Complete the release, repin and distribution prerequisites above. Keep the
   legacy `config-daemon-*.toml`, `config-serve-*.toml`, `forward-daemon@.service`
   and `forward-tunnel@.service` in place during steps 1 through 3.

2. Laptop: one `forward-daemon.service` with every VM's IP. Disable the `@` units.

   Run the one laptop command above. The installer resolves its own address with
   `tailscale ip -4` and every name in `hosts.toml` with `tailscale ip -4 <name>`.
   It writes literal IPv4 addresses, not names, into
   `~/.config/forward/config.toml`, with the shared policy from
   `daemon-policy.toml`. It disables all installed or loaded
   `forward-daemon@<pair>.service` and `forward-tunnel@<pair>.service` instances,
   including retired names, without removing their files. It does not create a
   key, SSH block, tunnel, port allocation or port-443 listener.

3. Each VM: an installer-made `config-serve`, then `forward doctor`.

   On each VM in `hosts.toml`, run `bash ~/.dotfiles/installers/forward.sh serve`,
   then `forward doctor`. The installer requires the VM's short hostname in
   `dev_vms`, uses its own `tailscale ip -4` address for `listen`, and resolves the
   inventory's laptop name to the literal `peer`. The generated serve config is
   `~/.config/forward/config.toml`, not a committed machine-specific file. Before
   starting serve, the installer stops and disables an installed or loaded
   `pcscd-bridge.service`, including an active-but-disabled bridge, so Forward
   can own `~/.pcscd/pcscd.comm`. It retains the bridge's unit file. A failed
   ownership transition fails the install without starting serve. Both roles
   use Forward's default ports: URL 12800, callback bridge 12801, preview 12802,
   browser relay 12803, PC/SC 12804, grant feed 12805 and audio 12806. The callback
   lease is 300 seconds.

   Verify the URL, callback, browser-grant, PC/SC and audio channels in `doctor`;
   a locked browser relay with no grant is normal. For a browser session, request
   `forward browser grant --ttl 30m` and use its printed `app.cdp_url`. No ambient
   browser relay endpoint is installed. New agent boxes read the canonical serve
   config and relay only its exact live loopback endpoints: preview, callback
   bridge and grant feed on `listen`; URL, browser relay, PC/SC and audio on
   `peer`. Custom channel ports outside 128xx are supported. Conflicting gateway
   destinations fail discovery, including conflicts with metadata, Envoy or
   other loopback-service relays; identical routes are not installed twice. The
   gateway identifies targets by port only, so assign distinct channel ports.
   Direct tailnet addresses route normally; the box does not bind the host's
   tailnet IP.

4. Delete the per-pair tomls, `forward-tunnel@.service`, the tunnel branch, the ssh blocks, the 443 drop-in and the `authorized_keys` line.

   Do this only after every VM passes step 3. Remove the obsolete daemon template
   `forward-daemon@.service` and its installed per-pair config/unit links too.
   Identify the Forward tunnel entries explicitly; leave unrelated SSH blocks,
   keys, `authorized_keys` entries and sshd settings alone. Steps 1 through 3 do
   not perform this cleanup, and this installer does not remove these artifacts.

5. Optional: add the ACL.

   If wanted, restrict tailnet TCP 12800–12806 between the laptop and the selected
   VMs. This is a Tailscale policy change, not an installer action. Forward's
   literal peer admission still rejects nodes outside the configured peer set.

## Inventory and lookup failures

`hosts.toml` is the one committed VM list. `laptop` is the laptop's short
Tailscale name; `dev_vms` holds the VM names. Adding a VM requires one list entry,
then reinstalling daemon on the laptop and serve on that VM. A rebuilt VM needs
those installers rerun to refresh its literal address. Retiring a VM requires
removing its entry and reinstalling daemon; no new per-pair file is needed.

Names must be distinct short Tailscale names. The installer rejects an unknown
serve host, duplicate names or resolved addresses, empty inventory, and any
lookup that does not return one IPv4 address in Tailscale's `100.64.0.0/10` range.
For a failed name, run the `tailscale ip -4 <name>` command shown in the error and
`tailscale status`, correct the inventory or tailnet membership, then rerun.
Resolution does not require the remote VM to be online if Tailscale still knows
its address. A failed lookup leaves the prior active config and unit state
unchanged. A successful config replacement is atomic and does not write through
a legacy config symlink into its committed target.

Every deliberate install restarts its role's units, even when config bytes are
unchanged. If an attempt replaces config and then fails while updating services,
correct the reported service error and rerun the same installer. The restart
applies the current peer set, including removals, instead of leaving the old
running daemon's authorization in place.
