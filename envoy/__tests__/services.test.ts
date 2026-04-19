/// <reference types="bun-types" />

import { describe, expect, test } from "bun:test";
import { ENVOY_NATS_URL } from "../services";

describe("ENVOY_NATS_URL", () => {
  test("points at the tailnet hostname for the AWS Fargate NATS", () => {
    // All on-prem listeners connect to the single Fargate NATS over the
    // host's existing Tailscale connection. There is no on-prem NATS cluster.
    expect(ENVOY_NATS_URL).toBe("nats://envoy-nats:4222");
  });
});
