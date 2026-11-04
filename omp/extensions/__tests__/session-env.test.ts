import { afterEach, beforeEach, describe, expect, test } from "bun:test";
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

describe("the identity a session exports", () => {
	const ROOT = "01a1a3bd-26a0-72f1-9e6e-a7a1f6ee18fe";
	const LAUNCHER = "01a1a3b9-f15e-7307-af39-d0e9f1e3a3b4";
	const SUBAGENT_HEADER = "01a1a3bd-b5e5-713b-b06e-ccb715cbbcdb";
	const OWNER = `2026-11-03T23-27-24-832Z_${ROOT}`;
	let home = "";
	let bucket = "";
	let fakeJj = "";
	const overlayOf = (id: string) => path.join(home, ".cache", "omp", "jj", `omp-attribution-${id}.toml`);
	beforeEach(() => {
		home = mkdtempSync(path.join(os.tmpdir(), "session-env-home-"));
		dirs.push(home);
		bucket = path.join(home, ".omp", "agent", "sessions", "--work--");
		mkdirSync(path.join(bucket, OWNER, "Probe"), { recursive: true });
		writeFileSync(path.join(bucket, `${OWNER}.jsonl`), "");
		// Like jj: `config path --user` prints JJ_CONFIG's entries when it is set. With
		// FAKE_JJ_FAIL set it fails, as jj does on a malformed user config.
		fakeJj = path.join(home, "bin");
		mkdirSync(fakeJj);
		writeFileSync(
			path.join(fakeJj, "jj"),
			`#!/bin/sh\n[ -n "$FAKE_JJ_FAIL" ] && exit 1\nif [ -n "$JJ_CONFIG" ]; then printf '%s\\n' "$JJ_CONFIG" | tr ':' '\\n'; else echo /u/default.toml; fi\n`,
		);
		chmodSync(path.join(fakeJj, "jj"), 0o755);
	});

	type Exported = { id: string | null; trailers: string | null; jjConfig: string | null; said: string };
	/**
	 * One session_start in a fresh process. `inherited` is the identity a launching session left
	 * in the environment: its id, and the JJ_CONFIG chain it passed down.
	 */
	async function start(
		ctx: { kind: "main" | "sub"; file?: string; hasUI?: boolean },
		inherited?: { id: string; jjConfig: string },
		extraEnv: Record<string, string> = {},
	): Promise<Exported> {
		const env: Record<string, string | undefined> = { ...process.env, HOME: home, PATH: `${fakeJj}:${process.env.PATH ?? ""}` };
		// The child's identity is only what `inherited` gives it, and its gh and gcloud
		// directories resolve under the scratch home.
		for (const key of ["OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "JJ_CONFIG", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "CLOUDSDK_CONFIG", "GOOGLE_APPLICATION_CREDENTIALS"]) {
			delete env[key];
		}
		if (inherited) {
			env.OMP_SESSION_ID = inherited.id;
			env.PI_COMMIT_TRAILERS = `Omp-Session: ${inherited.id}`;
			env.JJ_CONFIG = inherited.jjConfig;
		}
		Object.assign(env, extraEnv);
		const child = Bun.spawn([process.execPath, path.join(import.meta.dir, "session-env-driver.ts"), JSON.stringify(ctx)], {
			env,
			stdout: "pipe",
			stderr: "pipe",
		});
		const [out, err, code] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
		if (code !== 0) throw new Error(`driver exited ${code}: ${err}`);
		return JSON.parse(out) as Exported;
	}
	const fromLauncher = () => ({ id: LAUNCHER, jjConfig: `/u/config.toml:${overlayOf(LAUNCHER)}` });

	test.each([
		["a top-level transcript", () => path.join(bucket, `${OWNER}.jsonl`)],
		["a subagent transcript resumed on its own", () => path.join(bucket, OWNER, "Probe.jsonl")],
		["a nested subagent's transcript resumed on its own", () => path.join(bucket, OWNER, "Probe", "Probe.Reader.jsonl")],
	])("%s commits as the session the transcript belongs to, not as the session that launched it", async (_name, file) => {
		// Before: a resumed subagent transcript kept the launcher's id, so its commits named a
		// session that did not write them. Its own header id (SUBAGENT_HEADER) is not exported
		// either: `omp --resume <it>` finds no such session.
		const exported = await start({ kind: "main", file: file() }, fromLauncher());
		expect(exported).toMatchObject({
			id: ROOT,
			trailers: `Omp-Session: ${ROOT}`,
			jjConfig: `/u/config.toml:${overlayOf(ROOT)}`,
		});
		expect(await Bun.file(overlayOf(ROOT)).text()).toContain(`commit_trailers = '"Omp-Session: ${ROOT}"'`);
		expect(JSON.stringify(exported)).not.toContain(SUBAGENT_HEADER);
	});

	test("a subagent transcript resumed from a clean pane is attributed to its session", async () => {
		const exported = await start({ kind: "main", file: path.join(bucket, OWNER, "Probe.jsonl") });
		expect(exported).toMatchObject({ id: ROOT, jjConfig: `/u/default.toml:${overlayOf(ROOT)}` });
	});

	test("a launcher chain of nothing but its overlay is replaced by the user chain, not kept", async () => {
		const exported = await start(
			{ kind: "main", file: path.join(bucket, OWNER, "Probe.jsonl") },
			{ id: LAUNCHER, jjConfig: overlayOf(LAUNCHER) },
		);
		expect(exported.jjConfig).toBe(`/u/default.toml:${overlayOf(ROOT)}`);
	});

	test("a subagent running inside its parent's process keeps the parent's identity", async () => {
		const exported = await start({ kind: "sub", file: path.join(bucket, OWNER, "Probe.jsonl") }, fromLauncher());
		expect(exported).toMatchObject({ id: LAUNCHER, trailers: `Omp-Session: ${LAUNCHER}`, jjConfig: fromLauncher().jjConfig });
	});

	test.each([
		["names no session", false, () => path.join(home, "notes", "handmade.jsonl")],
		// A directory shaped like a session's, with no transcript beside it, is not a session.
		["sits under a directory that only looks like a session's", false, () => path.join(home, `2026-01-01T00-00-00-000Z_${LAUNCHER}`, "sessions", "orphan.jsonl")],
		[
			"sits under a session-shaped directory whose .jsonl sibling is a directory, not a transcript",
			false,
			() => {
				mkdirSync(path.join(home, `2026-01-01T00-00-00-000Z_${LAUNCHER}.jsonl`), { recursive: true });
				return path.join(home, `2026-01-01T00-00-00-000Z_${LAUNCHER}`, "sessions", "orphan.jsonl");
			},
		],
		["names no session (TUI)", true, () => path.join(home, "notes", "handmade.jsonl")],
	])("a session whose transcript %s drops the inherited identity and says so", async (_name, hasUI, file) => {
		const exported = await start({ kind: "main", file: file(), hasUI }, fromLauncher());
		// Emptied rather than unset, so the bash tool's commands lose the launcher's values; the
		// user chain stays, since an empty JJ_CONFIG would drop commit signing.
		expect(exported).toMatchObject({ id: "", trailers: "", jjConfig: "/u/config.toml" });
		expect(exported.said).toContain(file());
		expect(exported.said).toContain("no Omp-Session trailer");
	});

	test("an unattributed session whose inherited chain is only the launcher's overlay keeps no overlay", async () => {
		const exported = await start({ kind: "main", file: path.join(home, "notes", "handmade.jsonl") }, { id: LAUNCHER, jjConfig: overlayOf(LAUNCHER) });
		expect(exported).toMatchObject({ id: "", trailers: "", jjConfig: "/u/default.toml" });
	});

	// jj fails `config path --user` on a malformed user config. Unsetting JJ_CONFIG then is no
	// fix: the bash tool's commands still saw the launcher's overlay-only chain (round-2 review).
	test("with jj unable to name the user config, an unattributed session gets jj's default chain, not the launcher's", async () => {
		const exported = await start(
			{ kind: "main", file: path.join(home, "notes", "handmade.jsonl") },
			{ id: LAUNCHER, jjConfig: overlayOf(LAUNCHER) },
			{ FAKE_JJ_FAIL: "1" },
		);
		expect(exported).toMatchObject({
			id: "",
			jjConfig: `${path.join(home, ".jjconfig.toml")}:${path.join(home, ".config", "jj", "config.toml")}`,
		});
	});

	test("with jj unable to name the user config, a resumed subagent transcript still carries its own trailer", async () => {
		const exported = await start(
			{ kind: "main", file: path.join(bucket, OWNER, "Probe.jsonl") },
			{ id: LAUNCHER, jjConfig: overlayOf(LAUNCHER) },
			{ FAKE_JJ_FAIL: "1" },
		);
		expect(exported.jjConfig).toBe(`${path.join(home, ".jjconfig.toml")}:${path.join(home, ".config", "jj", "config.toml")}:${overlayOf(ROOT)}`);
	});
});
