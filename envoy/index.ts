import * as command from "@pulumi/command";
import type * as docker from "@pulumi/docker";
import * as pulumi from "@pulumi/pulumi";
import * as envoyInfra from "@sjawhar/envoy-infra";
import type { MachineConfig } from "./machines";
import { createProvider } from "./machines";

const cfg = new pulumi.Config("envoy");

// Stack configuration
const registry = cfg.require("registry");
const imageTag = cfg.require("imageTag");
const machines = cfg.requireObject<MachineConfig[]>("machines");
// The NATS URL each listener of this stack dials. prod's listeners reach the AWS Fargate NATS by
// its tailnet name (a Tailscale sidecar managed in agent-c's meta/infra); the laptop stack's
// reaches it by its VPC name, through the laptop's route into Trajectory's VPC, so it works on
// either tailnet. On-prem nodes do not run their own NATS.
const natsUrl = cfg.require("natsUrl");

// Registry auth for GHCR (private packages)
const registryAuth: docker.types.input.ProviderRegistryAuth[] = [
  { address: registry, username: "sjawhar", password: cfg.requireSecret("ghcrToken") },
];

// Watchdog deployment is opt-in via ENVOY_DEPLOY_WATCHDOG=1 because it needs an
// SSH agent holding a key root accepts on the host (pulumi-command logs in as
// root). Listener container `restart: unless-stopped` already handles
// process-level recovery; the watchdog only catches the case where the
// container is fully removed (e.g. by accident).
const deployWatchdog = process.env.ENVOY_DEPLOY_WATCHDOG === "1";

// One listener per machine, from Legion's envoy-infra plugin. `adopt` names the
// root-level resources the program created before the plugin, so the listener
// takes over their state instead of replacing them.
for (const machine of machines) {
  const providers: pulumi.ProviderResource[] = [createProvider(machine, registryAuth)];
  let watchdog: envoyInfra.types.input.host.WatchdogArgs | undefined;
  if (machine.sshHost && deployWatchdog) {
    providers.push(new command.Provider(`command-${machine.name}`, {}));
    // The bare address: the component refuses a user or scheme.
    watchdog = { host: machine.sshHost.replace(/^ssh:\/\/([^@]*@)?/, "") };
  }
  new envoyInfra.host.Listener(
    machine.name,
    {
      machineId: machine.machineId,
      natsUrls: [natsUrl],
      image: `${registry}/envoy:${imageTag}`,
      hostBridge: "127.0.0.1",
      watchdog,
      adopt: {
        container: `listener-${machine.name}`,
        image: `envoy-image-${machine.name}`,
        // The component refuses adopt.watchdog without a watchdog to adopt it into.
        ...(watchdog ? { watchdog: `watchdog-${machine.name}` } : {}),
      },
    },
    { providers }
  );
}
