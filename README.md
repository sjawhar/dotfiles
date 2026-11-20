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
| `ssh oryx true` | A browser tab may open for the Cloudflare Access sign-in. Then it prints nothing and returns to the prompt with exit 0. If it prints `Permission denied (publickey)`, give an agent the public-key output from `ssh-add -L` so the agent can add the key on oryx. |
| `ssh -t oryx '~/.local/bin/oryx-edge-cutover'` | Touch the YubiKey when asked. Its last line is `oryx-edge-cutover: complete`. |
| `bash ~/.dotfiles/installers/forward.sh daemon` | One `forward.sh daemon: oryx: <step>: <what it did>` line per step. Its last line is `forward.sh daemon: oryx: connected: forward-oryx is running`. |
| `ssh oryx-ssm true` | It prints nothing from `true` and exits 0. The Session Manager ProxyCommand can print its own lines. |

To rotate the service token, run `bash ~/.dotfiles/installers/forward.sh daemon`
again.

## SSH to oryx

oryx has one SSH hostname, `oryx.thecybermonk.com`, which Cloudflare Access sends
to sshd on oryx's port 22. `ssh oryx` uses the login key in 1Password.
`forward-oryx` uses `forward/forward-oryx.ssh-config`, its own key and the
service token. It never shares an SSH connection with `ssh oryx`. The phone
setup below is a backup for when you are away from the laptop.

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
follows `origin/main` every 10 minutes.
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
