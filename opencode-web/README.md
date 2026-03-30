# OpenCode Web UI

Serves the OpenCode web UI over Tailscale using Caddy + a dedicated `opencode serve` backend.

## Architecture

```
Phone/Browser → Tailscale HTTPS (:443) → Caddy (:8080)
    ├── static files → /home/ubuntu/opencode-web-frontend/
    └── API routes  → reverse_proxy → opencode serve (:4097)
```

- **Caddy** serves the built frontend and proxies API requests to the backend. Same-origin — no CORS needed.
- **opencode serve** runs as a systemd user service with GROQ_API_KEY (voice) and OPENCODE_DISABLE_CHANNEL_DB=1 (main DB).
- **Tailscale serve** exposes Caddy over HTTPS on the tailnet.

## Files

- `opencode-web-serve` — daemon wrapper: decrypts secrets via sops, resolves mise-installed binary, execs `opencode serve`
- `opencode-web.service` — systemd user service unit
- `Caddyfile` — Caddy reverse proxy + static file server config

## Setup (one-time)

```bash
# Symlink systemd service
mkdir -p ~/.config/systemd/user
ln -sf ~/.dotfiles/opencode-web/opencode-web.service ~/.config/systemd/user/opencode-web.service
systemctl --user daemon-reload
systemctl --user enable opencode-web

# Start backend
systemctl --user start opencode-web

# Start Caddy
caddy start --config ~/.dotfiles/opencode-web/Caddyfile

# Tailscale serve
sudo tailscale serve --bg --https 443 http://127.0.0.1:8080
```

## Frontend assets

The frontend is extracted from the `gh-pages` branch of the opencode repo:

```bash
TAG=$(~/.dotfiles/bin/mise current 'github:sjawhar/opencode')
cd ~/opencode/default
jj git fetch --remote origin
git archive origin/gh-pages -- "v${TAG}/" | tar -x -C /home/ubuntu/opencode-web-frontend --strip-components=1

# Rewrite absolute asset paths to root-relative
sed -i "s|/opencode/v${TAG}/||g" /home/ubuntu/opencode-web-frontend/index.html
```

## Upgrade

```bash
# 1. Upgrade the binary
mise upgrade 'github:sjawhar/opencode'

# 2. Refresh frontend assets (same commands as above)

# 3. Restart backend
systemctl --user restart opencode-web
```

## Logs

```bash
journalctl --user -u opencode-web -f
```

## URL

```
https://sami-agents-mx.tailb86685.ts.net/
```
