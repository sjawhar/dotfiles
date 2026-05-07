import * as command from "@pulumi/command";
import * as pulumi from "@pulumi/pulumi";
import type { MachineConfig } from "./machines";

/**
 * Deploy a systemd watchdog on an on-prem machine that detects and recovers
 * from container removal. Runs every 60s via systemd timer.
 *
 * The watchdog checks if the envoy-listener container exists and is running.
 * If stopped: `docker start`. If removed: `docker run` with saved config.
 *
 * Config is written to /etc/envoy/watchdog.json with the container parameters.
 * The watchdog script, systemd unit, and timer are deployed via SSH.
 */
export function deployWatchdog(
  machine: MachineConfig,
  containerEnvs: string[],
  image: string,
  dependsOn: pulumi.Resource[]
): command.remote.Command {
  // Only deploy on machines with SSH access (on-prem, not Fargate).
  if (!machine.sshHost) {
    throw new Error(`Watchdog requires SSH — machine ${machine.name} has no sshHost`);
  }

  const connection: command.types.input.remote.ConnectionArgs = {
    host: machine.sshHost.replace("ssh://", ""),
  };

  // Build the env file content from the container envs.
  const envFileContent = containerEnvs.join("\n");

  // Watchdog config JSON.
  const watchdogConfig = JSON.stringify(
    {
      container_name: "envoy-listener",
      image,
      env_file: "/etc/envoy/listener.env",
      network_mode: "host",
      healthcheck_cmd: "curl -sf http://127.0.0.1:9020/healthz",
    },
    null,
    2
  );

  // The watchdog script (self-contained, no external dependencies beyond docker + jq).
  const watchdogScript = `#!/usr/bin/env bash
# envoy-watchdog — detects and recovers from container removal.
# Deployed by Pulumi. Run by systemd timer every 60s.
set -euo pipefail

CONFIG="/etc/envoy/watchdog.json"
CONTAINER_NAME="envoy-listener"

check_running() {
    local state
    state=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "missing")
    [ "$state" = "running" ]
}

if check_running; then
    exit 0
fi

STATE=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "missing")
echo "envoy-watchdog: container is $STATE — recovering"

case "$STATE" in
    exited|created|paused)
        docker start "$CONTAINER_NAME"
        ;;
    missing)
        IMAGE=$(jq -r '.image' "$CONFIG")
        ENV_FILE=$(jq -r '.env_file' "$CONFIG")
        NETWORK=$(jq -r '.network_mode' "$CONFIG")
        HC_CMD=$(jq -r '.healthcheck_cmd' "$CONFIG")

        ARGS=(--name "$CONTAINER_NAME" --restart unless-stopped --network "$NETWORK" --env-file "$ENV_FILE")

        if [ -n "$HC_CMD" ] && [ "$HC_CMD" != "null" ]; then
            ARGS+=(--health-cmd "$HC_CMD" --health-interval 10s --health-timeout 3s --health-retries 3 --health-start-period 30s)
        fi

        docker run -d "\${ARGS[@]}" "$IMAGE"
        ;;
    *)
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        exec "$0"  # re-exec to hit 'missing' branch
        ;;
esac
echo "envoy-watchdog: recovery complete"
`;

  const systemdUnit = `[Unit]
Description=Envoy Listener Watchdog
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/envoy-watchdog
`;

  const systemdTimer = `[Unit]
Description=Envoy Listener Watchdog Timer

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
`;

  // Deploy everything in one SSH command.
  return new command.remote.Command(
    `watchdog-${machine.name}`,
    {
      connection,
      create: pulumi.interpolate`
        set -e
        mkdir -p /etc/envoy

        # Write env file
        cat > /etc/envoy/listener.env << 'ENVEOF'
${envFileContent}
ENVEOF

        # Write watchdog config
        cat > /etc/envoy/watchdog.json << 'CFGEOF'
${watchdogConfig}
CFGEOF

        # Write watchdog script
        cat > /usr/local/bin/envoy-watchdog << 'SCRIPTEOF'
${watchdogScript}
SCRIPTEOF
        chmod +x /usr/local/bin/envoy-watchdog

        # Write systemd unit
        cat > /etc/systemd/system/envoy-watchdog.service << 'UNITEOF'
${systemdUnit}
UNITEOF

        # Write systemd timer
        cat > /etc/systemd/system/envoy-watchdog.timer << 'TIMEREOF'
${systemdTimer}
TIMEREOF

        # Enable and start the timer
        systemctl daemon-reload
        systemctl enable envoy-watchdog.timer
        systemctl start envoy-watchdog.timer
        echo "Watchdog deployed on ${machine.name}"
      `,
      delete: `
        systemctl stop envoy-watchdog.timer 2>/dev/null || true
        systemctl disable envoy-watchdog.timer 2>/dev/null || true
        rm -f /usr/local/bin/envoy-watchdog /etc/systemd/system/envoy-watchdog.{service,timer} /etc/envoy/watchdog.json /etc/envoy/listener.env
        systemctl daemon-reload
      `,
    },
    { dependsOn }
  );
}
