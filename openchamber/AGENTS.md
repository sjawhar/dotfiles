# OpenChamber mobile stack

This directory packages Sami's phone-facing OpenChamber stack. `openchamber-serve.service` runs the fallback OpenCode backend on `127.0.0.1:5096`; `openchamber-fanin.service` discovers local Envoy sessions and presents the merged OpenCode API on `127.0.0.1:5199`; `openchamber-web.service` exposes the stock OpenChamber web app on `127.0.0.1:3210`.

The fan-in proxy merges backend SSE and status data, routes session requests to their owner, and falls back to :5096. Tailscale owns external routing separately and must not be configured here.

The stack runs as systemd user units, so user lingering must stay enabled (`loginctl enable-linger`) or the whole stack dies when the last login session ends and stays down after reboots. The installer enables it; do not disable it while these units are in use.

Deploy with `install.sh`, which links the user units and creates the password env file only when absent. Then use `systemctl --user daemon-reload` and enable/restart the three units in dependency order. The password belongs only in `~/.config/openchamber-stack/env`, never this repository.
