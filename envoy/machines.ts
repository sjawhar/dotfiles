import * as docker from "@pulumi/docker";

export interface ListenerConfig {
  webhooks?: WebhookConfig;
}

export interface WebhookConfig {
  github?: boolean;
  slack?: boolean;
  ghostwispr?: boolean;
}

export interface MachineConfig {
  name: string;
  sshHost?: string;
  machineId: string;
  listener: ListenerConfig;
}

export function createProvider(
  machine: MachineConfig,
  registryAuth?: docker.types.input.ProviderRegistryAuth[]
): docker.Provider {
  return new docker.Provider(`docker-${machine.name}`, {
    host: machine.sshHost,
    registryAuth,
  });
}
