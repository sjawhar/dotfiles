# Dotfiles

Read [AGENTS.md](AGENTS.md) before changing these files.

## Laptop

After setup, use `ssh oryx` for a terminal on oryx. The browser asks for a
Google sign-in about once a day. Use `ssh oryx-ssm` when Cloudflare is down; run
`aws login --profile personal` first if AWS says the sign-in expired. When
forward to oryx seems broken, run `systemctl --user status forward-oryx` or
`systemctl --user restart forward-oryx`. Forward to oryx needs nothing else
from you.

Before these commands, this laptop already has `cloudflared` and
`~/.ssh/config` linked from earlier runs. A new laptop gets both from
`bash ~/.dotfiles/laptop/install.sh`, which runs command 4.

| Command | What it prints when it works |
| --- | --- |
| `~/.dotfiles/scripts/dotfiles-sync local` | `== local`, then `<hostname> on <commit>`, then `tools: <command> passed in <directory>` lines |
| `ssh oryx true` | A browser tab may open for the Cloudflare Access sign-in. ssh uses the 1Password key when 1Password's SSH agent has one turned on, otherwise the YubiKey key `~/.ssh/id_ed25519_sk_oryx_sami` with one touch (and that key's passphrase, if you set one). Then it prints nothing and returns to the prompt with exit 0. If it still says `Permission denied (publickey)`, paste to an agent the output of the command under the table, which names only the public keys ssh offered. If it says `Too many authentication failures`, 1Password offered too many keys before the YubiKey key: turn off in 1Password's SSH agent the keys that are not for oryx, and run it again. |
| `ssh -t oryx '~/.local/bin/oryx-edge-cutover'` | Touch the YubiKey when asked. The run ends with `oryx-edge-cutover: complete`, followed by the line `oryx-edge-cutover: log <path>`, which names its log file. |
| `bash ~/.dotfiles/installers/forward.sh daemon` | One `forward.sh daemon: oryx: <step>: <what it did>` line per step. Its last line is `forward.sh daemon: oryx: connected: forward-oryx is running`. |
| `ssh oryx-ssm true` | It prints nothing from `true` and exits 0. The Session Manager ProxyCommand can print its own lines. |

If `ssh oryx true` says `Permission denied (publickey)`, run this and paste its
output to an agent:

```bash
ssh -v oryx true 2>&1 | grep -E 'Offering|Server accepts|denied'
```

To rotate the service token, run `bash ~/.dotfiles/installers/forward.sh daemon`
again.

## SSH to oryx

oryx has one SSH hostname, `oryx.thecybermonk.com`, which Cloudflare Access sends
to sshd on oryx's port 22. `ssh oryx` offers the 1Password key first, then the
YubiKey key (`~/.ssh/id_ed25519_sk_oryx_sami`). `forward-oryx` uses
`forward/forward-oryx.ssh-config`, its own key and the service token. It never
shares an SSH connection with `ssh oryx`. The phone setup below is a backup for
when you are away from the laptop.

## Oryx tunnel ports

| Port on oryx `127.0.0.1` | Direction | Laptop end | Use |
| --- | --- | --- | --- |
| 12800 | RemoteForward | 127.0.0.1:12800 | URL (`forward open`) |
| 12801 | LocalForward | 127.0.0.1:12801 | Callback bridge and `forward port` |
| 12802 | LocalForward | 127.0.0.1:12802 | File preview |
| 12803 | RemoteForward | 127.0.0.1:12803 | Browser relay |
| 12804 | RemoteForward | 127.0.0.1:12804 | PC/SC for YubiKey use |
| 12805 | LocalForward | 127.0.0.1:12805 | Browser grant feed |
| 12806 | RemoteForward | 127.0.0.1:12806 | Audio |

## Oryx

Run `bash ~/.dotfiles/oryx/install.sh` once on oryx. It arms the
`dotfiles-follow` user timer with oryx's drop-in, so oryx's `~/.dotfiles`
follows `origin/main` every 10 minutes and its alerts go to the HOME controller.
It needs no Tailscale; `oryx-edge-cutover` writes oryx's forward configuration.
Run it as `agent`, the sudo user: it also enables and starts `bluetooth` and
turns on lingering for `sami`, because the Galaxy A15 agent phone line pairs with
oryx over Bluetooth and its bridge runs in `sami`'s user session.

`oryx-tunnel-enrol`, which `bash ~/.dotfiles/installers/forward.sh daemon` runs,
writes the line in `oryx/oryx-tunnel.key-options` in front of the laptop's
tunnel key in `agent`'s `~/.ssh/authorized_keys`. sshd on oryx must also set
`AllowStreamLocalForwarding no`. The tunnel reaches sshd on port 22, so this
setting applies to every session there.

## Phone

The phone is a backup way into oryx, through the same hostname, for when you
are away from the laptop.

Install Termux. Termux includes the bootstrap `curl` command. If `curl` is
unavailable, run `pkg install -y curl`.

```bash
f=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/sjawhar/dotfiles/main/phone/termux-setup -o "$f" && bash "$f"; s=$?; rm -f "$f"; [ "$s" -eq 0 ]
```

The command runs `bash` only after `curl` downloads the complete script. It
prints the public key for the phone. Put the printed public key in `agent`'s
`~/.ssh/authorized_keys` on oryx.
