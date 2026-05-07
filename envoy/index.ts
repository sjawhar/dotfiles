import type * as docker from "@pulumi/docker";
import * as pulumi from "@pulumi/pulumi";
import { pullImages } from "./images";
import type { MachineConfig } from "./machines";
import { createProvider } from "./machines";
import { ENVOY_NATS_URL, createListener } from "./services";
import { deployWatchdog } from "./watchdog";

const cfg = new pulumi.Config("envoy");

// Stack configuration
const registry = cfg.require("registry");
const imageTag = cfg.require("imageTag");
const machines = cfg.requireObject<MachineConfig[]>("machines");

// Secrets — webhook secrets are optional at the Pulumi level.
// Go startup validates required secrets via ENVOY_WEBHOOKS config-gating.
const githubWebhookSecret = cfg.getSecret("githubWebhookSecret");
const slackSigningSecret = cfg.getSecret("slackSigningSecret");
const ghostWisprSigningSecret = cfg.getSecret("ghostWisprSigningSecret");

const secrets = {
  githubWebhookSecret,
  slackSigningSecret,
  ghostWisprSigningSecret,
};

// Registry auth for GHCR (private packages)
const registryAuth: docker.types.input.ProviderRegistryAuth[] = [
  { address: registry, username: "sjawhar", password: cfg.requireSecret("ghcrToken") },
];

// Deploy to each machine
for (const machine of machines) {
  const provider = createProvider(machine, registryAuth);
  const images = pullImages(provider, machine, registry, imageTag);

  // Listener — on ALL machines
  const listener = createListener(provider, machine, images.envoy, secrets, []);

  // Watchdog deployment is opt-in via ENVOY_DEPLOY_WATCHDOG=1 because it
  // requires SSH agent with the right keys (not always available in the
  // deploy environment). Listener container `restart: unless-stopped`
  // already handles process-level recovery; the watchdog only catches the
  // case where the container is fully removed (e.g. by accident).
  if (machine.sshHost && process.env.ENVOY_DEPLOY_WATCHDOG === "1") {
    const envs = [
      "PORT=9020",
      `ENVOY_MACHINE_ID=${machine.machineId}`,
      `NATS_URLS=${ENVOY_NATS_URL}`,
      "ENVOY_HOST_BRIDGE=127.0.0.1",
    ];
    deployWatchdog(machine, envs, `${registry}/envoy:${imageTag}`, [listener]);
  }
}
