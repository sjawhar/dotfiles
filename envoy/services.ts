import * as docker from "@pulumi/docker";
import * as pulumi from "@pulumi/pulumi";
import type { MachineConfig } from "./machines";

// All listeners connect to the AWS Fargate NATS over the host's existing
// Tailscale connection. The Fargate NATS hostname is exposed on the tailnet
// via a Tailscale sidecar managed in ~/agent-c/default/meta/infra
// (see envoy_nats.py). On-prem nodes do not run their own NATS.
export const ENVOY_NATS_URL = "nats://envoy-nats:4222";

// --- Pulumi resources ---

interface ServiceSecrets {
  githubWebhookSecret?: pulumi.Output<string>;
  slackSigningSecret?: pulumi.Output<string>;
  ghostWisprSigningSecret?: pulumi.Output<string>;
}

/**
 * Create the listener container on a machine. Runs on every host (host
 * networking). The listener is a stateless NATS subscriber that connects
 * outbound to `envoy-nats` over the host's Tailscale and serves
 * `127.0.0.1:9020` for local OpenCode session registration.
 */
export function createListener(
  provider: docker.Provider,
  machine: MachineConfig,
  image: docker.RemoteImage,
  secrets: ServiceSecrets,
  dependsOn: pulumi.Resource[]
): docker.Container {
  const webhookEnvs: pulumi.Input<string>[] = [];
  const webhooks = machine.listener.webhooks;
  if (webhooks) {
    const enabled: string[] = [];
    if (webhooks.github) enabled.push("github");
    if (webhooks.slack) enabled.push("slack");
    if (webhooks.ghostwispr) enabled.push("ghostwispr");
    if (enabled.length > 0) {
      webhookEnvs.push(`ENVOY_WEBHOOKS=${enabled.join(",")}`);
    }
    if (webhooks.github) {
      webhookEnvs.push(
        pulumi.interpolate`ENVOY_GITHUB_WEBHOOK_SECRET=${secrets.githubWebhookSecret}`
      );
      webhookEnvs.push("ENVOY_GITHUB_MENTION_TRIGGER=@legion");
    }
    if (webhooks.slack) {
      webhookEnvs.push(
        pulumi.interpolate`ENVOY_SLACK_SIGNING_SECRET=${secrets.slackSigningSecret}`
      );
    }
    if (webhooks.ghostwispr && secrets.ghostWisprSigningSecret) {
      webhookEnvs.push(
        pulumi.interpolate`ENVOY_GHOSTWISPR_SIGNING_SECRET=${secrets.ghostWisprSigningSecret}`
      );
    }
  }

  return new docker.Container(
    `listener-${machine.name}`,
    {
      name: "envoy-listener",
      image: image.imageId,
      command: ["/usr/local/bin/envoy-listener"],
      restart: "unless-stopped",
      networkMode: "host",
      envs: [
        "PORT=9020",
        `ENVOY_MACHINE_ID=${machine.machineId}`,
        `NATS_URLS=${ENVOY_NATS_URL}`,
        "ENVOY_HOST_BRIDGE=127.0.0.1",
        ...webhookEnvs,
      ],
      healthcheck: {
        tests: ["CMD", "curl", "-f", "http://127.0.0.1:9020/healthz"],
        interval: "10s",
        timeout: "3s",
        retries: 3,
        startPeriod: "30s",
      },
      wait: true,
      waitTimeout: 90,
    },
    {
      provider,
      dependsOn,
      deleteBeforeReplace: true,
    }
  );
}
