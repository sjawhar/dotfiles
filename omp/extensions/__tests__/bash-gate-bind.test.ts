import { expect, mock, test } from "bun:test";
import { ompModules } from "./omp-interceptor";

// bash-gate.ts imports omp's modules by package specifier, which `bun test` cannot resolve; point
// those specifiers at the files omp-interceptor.ts located, then import bash-gate dynamically so
// the mocks are in place before its imports resolve.
mock.module("@oh-my-pi/pi-coding-agent/exec/settings", () => ompModules.execSettings);
mock.module("@oh-my-pi/pi-coding-agent/tools/bash-interceptor", () => ompModules.interceptor);
mock.module("@oh-my-pi/pi-coding-agent/tools/shell-tokenize", () => ompModules.tokenize);
const { default: bashGate } = await import("../experiments/bash-gate");
const { cfgBashInterceptorEnabled } = ompModules.execSettings;

// A settings object that keeps runtime overrides as omp's does, read back through omp's own handle.
const overrides: Record<string, unknown> = { "bashInterceptor.enabled": true };
let revision = 0;
const settings = {
	get revision() {
		return revision;
	},
	valueCache: [],
	warnState: { invalid: new Map(), items: new Map() },
	rawValue: (setting: { id: string }) => overrides[setting.id],
	writeValue(setting: { id: string }, value: unknown, layer: string) {
		expect(layer).toBe("override");
		overrides[setting.id] = value;
		revision++;
	},
};

test("binding turns omp's built-in interceptor off in the process's settings", () => {
	expect(cfgBashInterceptorEnabled.get(settings)).toBe(true);
	bashGate({ pi: { settings }, on() {}, getActiveTools: () => [] } as never, () => false);
	expect(cfgBashInterceptorEnabled.get(settings)).toBe(false);
});
