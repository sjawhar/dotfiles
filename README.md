# Dotfiles

Read [AGENTS.md](AGENTS.md) before changing these files.

## Laptop

Run this command on the laptop. It works from an old `~/.dotfiles` checkout.

```bash
git -C ~/.dotfiles fetch -q origin main && s=$(git -C ~/.dotfiles show origin/main:laptop/oryx-tunnel-setup) && bash -c "$s" oryx-tunnel-setup
```

During the command, sign in to Cloudflare Access in the browser. Touch the
YubiKey when the command asks for it. Answer `y` or `N` for the browser and
sound checks. The command installs Cloudflare Access's SSH client, replaces the
laptop SSH configuration with the Cloudflare hosts, copies the tunnel service
token from oryx, writes oryx's loopback forward configuration, starts the
forward user units, and ends the old tunnel.

Run `bash ~/.dotfiles/laptop/oryx-tunnel-setup` again to repeat the setup. Each
run writes a mode-0600 report at
`~/.local/state/oryx-laptop/report-<UTC>.txt` on the laptop, then uploads it to
the same path on oryx after the login probe succeeds. The command prints the
oryx report path. If the upload fails, it prints the local report path.

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
It needs no Tailscale; the laptop command above writes oryx's forward config.

The line in `oryx/oryx-tunnel.key-options` is installed on oryx by hand: it goes
in front of the laptop's tunnel key in `agent`'s `~/.ssh/authorized_keys`. sshd
on oryx's port 2222 must also set `AllowStreamLocalForwarding no`.

## Phone

Install Termux. Termux includes the bootstrap `curl` command. If `curl` is
unavailable, run `pkg install -y curl`.

```bash
f=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/sjawhar/dotfiles/main/phone/termux-setup -o "$f" && bash "$f"; s=$?; rm -f "$f"; [ "$s" -eq 0 ]
```

The command runs `bash` only after `curl` downloads the complete script. It
prints the public key for the phone. Put the printed public key in `agent`'s
`~/.ssh/authorized_keys` on oryx.
