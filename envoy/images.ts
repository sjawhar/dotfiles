import * as docker from "@pulumi/docker";
import type { MachineConfig } from "./machines";

export interface MachineImages {
  envoy: docker.RemoteImage;
}

/**
 * Pull only the images a machine actually needs.
 *
 * Uses keepLocally: true to avoid deleting images on `pulumi destroy`.
 */
export function pullImages(
  provider: docker.Provider,
  machine: MachineConfig,
  registry: string,
  imageTag: string
): MachineImages {
  return {
    envoy: new docker.RemoteImage(
      `envoy-image-${machine.name}`,
      {
        name: `${registry}/envoy:${imageTag}`,
        keepLocally: true,
      },
      { provider }
    ),
  };
}
