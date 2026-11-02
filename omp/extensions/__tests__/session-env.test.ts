import { afterEach, describe, expect, test } from "bun:test";
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { agentGhConfigDir, agentGoogleCredentials, userConfigChain } from "../session-env";

const dirs: string[] = [];
/** An environment whose PATH finds a `jj` running `body` first. */
function withJj(body: string): NodeJS.ProcessEnv {
	const dir = mkdtempSync(path.join(os.tmpdir(), "session-env-jj-"));
	dirs.push(dir);
	writeFileSync(path.join(dir, "jj"), `#!/bin/sh\n${body}\n`);
	chmodSync(path.join(dir, "jj"), 0o755);
	return { ...process.env, PATH: `${dir}:${process.env.PATH ?? ""}` };
}
afterEach(() => {
	for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("userConfigChain", () => {
	test("chains the user config files jj prints, in order", async () => {
		expect(await userConfigChain(5_000, withJj("printf '/u/a.toml\\n/u/b.toml\\n'"))).toBe("/u/a.toml:/u/b.toml");
	});

	test("gives up on a jj that never answers, so session_start never waits on it", async () => {
		const started = Date.now();
		expect(await userConfigChain(200, withJj("exec sleep 30"))).toBeUndefined();
		expect(Date.now() - started).toBeLessThan(4_000);
	}, 5_000);
});

describe("agentGhConfigDir", () => {
	// A gh that runs past shims/gh reads GH_CONFIG_DIR; the user's own directory holds his login.
	const script = (body: string) => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "session-env-gh-"));
		dirs.push(dir);
		writeFileSync(path.join(dir, "gh-agent-config"), `#!/bin/sh\n${body}\n`);
		chmodSync(path.join(dir, "gh-agent-config"), 0o755);
		return path.join(dir, "gh-agent-config");
	};
	const make = () => script("echo /home/u/.config/gh-agent");

	test("points an unset GH_CONFIG_DIR, or the user's own directory, at the agent directory", () => {
		expect(agentGhConfigDir({ HOME: "/home/u", PATH: process.env.PATH }, make())).toBe("/home/u/.config/gh-agent");
		expect(
			agentGhConfigDir({ HOME: "/home/u", GH_CONFIG_DIR: "/home/u/.config/gh/", PATH: process.env.PATH }, make()),
		).toBe("/home/u/.config/gh-agent");
	});

	test("keeps a directory a launcher chose", () => {
		expect(agentGhConfigDir({ HOME: "/home/u", GH_CONFIG_DIR: "/legion/state/gh" }, make())).toBe("/legion/state/gh");
	});

	test("sets nothing when the directory cannot be made", () => {
		expect(agentGhConfigDir({ HOME: "/home/u", PATH: process.env.PATH }, script("exit 1"))).toBeUndefined();
	});
});

describe("agentGoogleCredentials", () => {
	// Without GOOGLE_APPLICATION_CREDENTIALS a client library reads the well-known ADC file, which
	// holds whoever last ran an ADC login: the user's own on sami-rocqperf since 2026-11-01.
	const home = (box: boolean) => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "session-env-adc-"));
		dirs.push(dir);
		mkdirSync(path.join(dir, ".config", "gcloud"), { recursive: true });
		if (box) writeFileSync(path.join(dir, ".config", "gcloud", "box_credential_config.json"), "{}");
		return dir;
	};
	// The machine kind, as scripts/on-google-vm answers it: by exit status alone.
	const NOT_GOOGLE = "/bin/false";
	const GOOGLE = "/bin/true";

	test("points an unset variable, or the well-known file, at the box identity", () => {
		const h = home(true);
		const box = path.join(h, ".config", "gcloud", "box_credential_config.json");
		expect(agentGoogleCredentials({ HOME: h }, NOT_GOOGLE)).toBe(box);
		expect(
			agentGoogleCredentials({ HOME: h, GOOGLE_APPLICATION_CREDENTIALS: `${h}/.config/gcloud/./application_default_credentials.json` }, NOT_GOOGLE),
		).toBe(box);
	});

	test("honours CLOUDSDK_CONFIG for both files", () => {
		const h = home(false);
		const cfg = path.join(home(true), ".config", "gcloud");
		const box = path.join(cfg, "box_credential_config.json");
		expect(agentGoogleCredentials({ HOME: h, CLOUDSDK_CONFIG: cfg }, NOT_GOOGLE)).toBe(box);
		expect(
			agentGoogleCredentials({
				HOME: h,
				CLOUDSDK_CONFIG: cfg,
				GOOGLE_APPLICATION_CREDENTIALS: path.join(cfg, "application_default_credentials.json"),
			}, NOT_GOOGLE),
		).toBe(box);
	});

	test("keeps a file a launcher chose", () => {
		const h = home(true);
		const own = path.join(h, ".config", "gcloud", "legacy_credentials", "sami@theorem.dev", "adc.json");
		expect(agentGoogleCredentials({ HOME: h, GOOGLE_APPLICATION_CREDENTIALS: own }, NOT_GOOGLE)).toBe(own);
		expect(agentGoogleCredentials({ HOME: h, GOOGLE_APPLICATION_CREDENTIALS: own }, GOOGLE)).toBe(own);
	});

	test("sets nothing on a machine with no box identity", () => {
		expect(agentGoogleCredentials({ HOME: home(false) }, NOT_GOOGLE)).toBeUndefined();
	});

	test("sets nothing on a Google VM, whose ADC is the metadata server", () => {
		// A box_credential_config.json there was carried over from an Azure box (dispatch://INFRA-993).
		const h = home(true);
		expect(agentGoogleCredentials({ HOME: h }, GOOGLE)).toBeUndefined();
		const wellKnown = path.join(h, ".config", "gcloud", "application_default_credentials.json");
		expect(agentGoogleCredentials({ HOME: h, GOOGLE_APPLICATION_CREDENTIALS: wellKnown }, GOOGLE)).toBe(wellKnown);
	});
});

describe("the Python eval kernel's environment", () => {
	// omp starts the Python eval kernel with its built-in allowlist only (fixed names plus the
	// LC_, XDG_ and PI_ prefixes, packages/coding-agent/src/eval/py/runtime.ts) and what
	// eval.envPassthrough in config.yml admits. A variable this extension exports that neither
	// admits never reaches a command run from an eval cell: agent-secrets lost the session that way.
	const OMP_BUILTIN_PREFIXES = ["LC_", "XDG_", "PI_"];

	test("admits every variable session-env exports", async () => {
		const source = await Bun.file(path.join(import.meta.dir, "..", "session-env.ts")).text();
		const exported = [...new Set(Array.from(source.matchAll(/process\.env\.([A-Z][A-Z0-9_]*)\s*=(?!=)/g), m => m[1]))];
		expect(exported).toEqual(
			expect.arrayContaining(["OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "JJ_CONFIG", "GH_CONFIG_DIR", "GOOGLE_APPLICATION_CREDENTIALS"]),
		);
		const config = Bun.YAML.parse(await Bun.file(path.join(import.meta.dir, "..", "..", "config.yml")).text()) as {
			eval?: { envPassthrough?: { names?: string[]; prefixes?: string[] } };
		};
		const { names = [], prefixes = [] } = config.eval?.envPassthrough ?? {};
		const allowedPrefixes = [...OMP_BUILTIN_PREFIXES, ...prefixes];
		const dropped = exported.filter(key => !names.includes(key) && !allowedPrefixes.some(prefix => key.startsWith(prefix)));
		expect(dropped).toEqual([]);
	});
});
