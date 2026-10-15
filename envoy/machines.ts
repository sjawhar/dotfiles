import * as docker from "@pulumi/docker";

export interface MachineConfig {
  name: string;
  sshHost?: string;
  machineId: string;
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
