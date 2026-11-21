import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, watch, writeFileSync } from "node:fs";
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
	const droppedByKernel = async (keys: string[]) => {
		const config = Bun.YAML.parse(await Bun.file(path.join(import.meta.dir, "..", "..", "config.yml")).text()) as {
			eval?: { envPassthrough?: { names?: string[]; prefixes?: string[] } };
		};
		const { names = [], prefixes = [] } = config.eval?.envPassthrough ?? {};
		const allowedPrefixes = [...OMP_BUILTIN_PREFIXES, ...prefixes];
		return keys.filter(key => !names.includes(key) && !allowedPrefixes.some(prefix => key.startsWith(prefix)));
	};

	test("admits every variable session-env exports", async () => {
		const source = await Bun.file(path.join(import.meta.dir, "..", "session-env.ts")).text();
		const exported = [...new Set(Array.from(source.matchAll(/process\.env\.([A-Z][A-Z0-9_]*)\s*=(?!=)/g), m => m[1]))];
		expect(exported).toEqual(
			expect.arrayContaining(["OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "JJ_CONFIG", "GH_CONFIG_DIR", "GOOGLE_APPLICATION_CREDENTIALS"]),
		);
		expect(await droppedByKernel(exported)).toEqual([]);
	});

	test("admits the cache directories and session paths the session launchers set", async () => {
		// scripts/agent-session and .bashrc put each tool's cache on the big disk, and give the
		// session its own scratch. A name the kernel drops sends a tool run from an eval cell back
		// to its home default: on 2026-11-21 `go test` from eval cells filled ~/.cache/go-build to
		// 475 GB, and without PLAYWRIGHT_SKIP_BROWSER_GC a `playwright install` from a cell deletes
		// browsers in the directory agent boxes share.
		const sessionVariables = [
			"GOCACHE",
			"GOMODCACHE",
			"UV_CACHE_DIR",
			"UV_LINK_MODE",
			"CARGO_HOME",
			"PIP_CACHE_DIR",
			"BUN_INSTALL_CACHE_DIR",
			"HF_HOME",
			"AGENT_SESSION_SCRATCH",
			"GOTMPDIR",
			"MISE_DATA_DIR",
			"MISE_CACHE_DIR",
			"PLAYWRIGHT_BROWSERS_PATH",
			"PLAYWRIGHT_SKIP_BROWSER_GC",
			"PUPPETEER_CACHE_DIR",
			"NPM_CONFIG_PREFIX",
		];
		expect(await droppedByKernel(sessionVariables)).toEqual([]);
		// Each one has a value to pass: a launcher exports it.
		const repo = path.join(import.meta.dir, "..", "..", "..");
		const launchers = (
			await Promise.all(
				[".bashrc", "scripts/agent-session"].map(file => Bun.file(path.join(repo, file)).text()),
			)
		).join("\n");
		const unset = sessionVariables.filter(name => !new RegExp(`\\bexport\\b[^\\n]*\\b${name}=`).test(launchers));
		expect(unset).toEqual([]);
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
		// directories resolve under the scratch home. It finds no secrets helper socket, so it
		// never names a session to this machine's real helper (session-env-register.test.ts
		// covers that call).
		for (const key of [
			"OMP_SESSION_ID",
			"PI_COMMIT_TRAILERS",
			"JJ_CONFIG",
			"GH_CONFIG_DIR",
			"XDG_CONFIG_HOME",
			"CLOUDSDK_CONFIG",
			"GOOGLE_APPLICATION_CREDENTIALS",
			"AGENT_SECRETS_HELPER_SOCK",
			"AGENTBOX_BOX",
		]) {
			delete env[key];
		}
		env.XDG_RUNTIME_DIR = home;
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

describe("the session file of a box", () => {
	// The box's runner (scripts/agentbox-run.ts) restarts the session this file names and no other
	// (dispatch://INFRA-1720), so it must name the main session of the omp the runner started, and
	// a reader must never see half of it. The timer rows wait in real time: the timer is the
	// extension's own setInterval, in the driver's process, where no fake clock reaches.
	const A = "01a1e000-0000-7000-8000-00000000000a";
	const B = "01a1e000-0000-7000-8000-00000000000b";
	const C = "01a1e000-0000-7000-8000-00000000000c";
	const SUB = "01a1e000-0000-7000-8000-0000000000aa";
	const ADVISOR = "01a1e000-0000-7000-8000-0000000000ad";
	const MAIN = { kind: "main", id: "Main" } as const;
	const SUBAGENT = { kind: "sub", id: "0-Task" } as const;
	// How an advisor reaches the advising session's handlers (omp's ExtensionAgentIdentity).
	const ADVISING = { kind: "sub", id: "advisor" } as const;
	const HOUR = 3_600_000;
	let home = "";
	let runtime = "";
	const transcript = (id: string) => path.join(home, ".omp", "agent", "sessions", "--work--", `2026-11-15T10-00-00-000Z_${id}.jsonl`);
	const reportOf = (pid: number, id: string, file: string) => `version=1\npid=${pid}\nsession=${id}\nfile=${file}\n`;
	beforeEach(() => {
		home = mkdtempSync(path.join(os.tmpdir(), "session-env-box-"));
		dirs.push(home);
		runtime = path.join(home, "run");
		mkdirSync(runtime);
	});

	type Agent = { kind: "main" | "sub"; id: string };
	type Step =
		| { event: string; agent: Agent; manager: string }
		| { set: string; id?: string; file?: string }
		| { sleep: number }
		| { hold: "open" | "read" };
	type Snapshot = { content: string; ino: number } | null;
	type Run = {
		pid: number;
		snapshots: Snapshot[];
		saved: { manager: string; id: string | null; held: string | null }[];
		holds: (string | null)[];
	};
	/**
	 * One script of events in a fresh process (session-env-file-driver.ts). `runner` is where
	 * AGENTBOX_RUNNER_PID points: at the process's own parent (the omp a box's runner started), at
	 * its grandparent through a shell (an `omp -p` that a tool runs in a box), or nowhere (an omp
	 * outside any box).
	 */
	async function run(
		script: { managers: Record<string, { id?: string; file?: string }>; steps: Step[] },
		{ runner = "parent", intervalMs = HOUR }: { runner?: "parent" | "grandparent" | "none"; intervalMs?: number } = {},
	): Promise<Run> {
		const env: Record<string, string | undefined> = {
			...process.env,
			HOME: home,
			XDG_RUNTIME_DIR: runtime,
			// A chain to inherit and a gh directory of its own: no jj and no gh-agent-config runs.
			JJ_CONFIG: "/u/config.toml",
			GH_CONFIG_DIR: path.join(home, "gh"),
			OMP_AGENTBOX_SESSION_MS: String(intervalMs),
		};
		for (const key of ["OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "AGENTBOX_RUNNER_PID", "XDG_CONFIG_HOME", "CLOUDSDK_CONFIG", "GOOGLE_APPLICATION_CREDENTIALS"]) {
			delete env[key];
		}
		if (runner !== "none") env.AGENTBOX_RUNNER_PID = String(process.pid);
		const driver = [process.execPath, path.join(import.meta.dir, "session-env-file-driver.ts"), JSON.stringify(script)];
		// Two commands, so the shell forks the driver rather than exec'ing it.
		const argv = runner === "grandparent" ? ["sh", "-c", '"$@"; exit $?', "sh", ...driver] : driver;
		const child = Bun.spawn(argv, { env, stdout: "pipe", stderr: "pipe" });
		const [out, err, code] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
		if (code !== 0) throw new Error(`driver exited ${code}: ${err}`);
		return JSON.parse(out) as Run;
	}

	test("sessionFileFollowsNewResumeAndBranch: each names its session whole, saved first, through a rename in the same directory", async () => {
		const seen: string[] = [];
		const watcher = watch(runtime, (_kind, name) => {
			if (name) seen.push(String(name));
		});
		// A reader of the session file holds a descriptor across the two writes that follow. A
		// rename gives the name a NEW file, so that descriptor still reads the whole report it
		// opened; a write in place would read through to the new bytes, and between the
		// truncation and the write to none of them. The read is taken after both writes have
		// returned, so nothing here depends on when a reader is scheduled.
		const { pid, snapshots, saved, holds } = await run({
			managers: { main: { id: A, file: transcript(A) } },
			steps: [
				{ event: "session_start", agent: MAIN, manager: "main" },
				{ hold: "open" },
				{ set: "main", id: B, file: transcript(B) },
				{ event: "session_switch", agent: MAIN, manager: "main" },
				{ set: "main", id: C, file: transcript(C) },
				{ event: "session_branch", agent: MAIN, manager: "main" },
				{ hold: "read" },
			],
		});
		watcher.close();
		expect(holds).toEqual([reportOf(pid, A, transcript(A))]);
		expect(snapshots.map((s) => s?.content ?? null)).toEqual([
			reportOf(pid, A, transcript(A)),
			reportOf(pid, A, transcript(A)),
			reportOf(pid, A, transcript(A)),
			reportOf(pid, B, transcript(B)),
			reportOf(pid, B, transcript(B)),
			reportOf(pid, C, transcript(C)),
			reportOf(pid, C, transcript(C)),
		]);
		// Each save comes before the write it is for: the file still names the session before.
		expect(saved).toEqual([
			{ manager: "main", id: A, held: null },
			{ manager: "main", id: B, held: reportOf(pid, A, transcript(A)) },
			{ manager: "main", id: C, held: reportOf(pid, B, transcript(B)) },
		]);
		// A rename puts a new file in place; a write in place keeps the old one.
		expect(snapshots[3]?.ino).not.toBe(snapshots[0]?.ino);
		expect(snapshots[5]?.ino).not.toBe(snapshots[3]?.ino);
		expect(seen).toContain(`agentbox-session.${pid}.tmp`);
		expect(readdirSync(runtime)).toEqual(["agentbox-session"]);
	}, 30_000);

	const everyTrigger = (agent: Agent, manager: string): Step[] =>
		["session_start", "session_switch", "session_branch", "turn_end", "tool_call"].map((event) => ({ event, agent, manager }));

	test.each([
		["an omp -p that a tool runs in a box, whose parent is a shell and not the runner", "grandparent"],
		["an omp outside any box", "none"],
	] as const)("noSessionFileFromANestedOmp: %s writes no session file and saves no session", async (_name, runner) => {
		const { snapshots, saved } = await run(
			{
				managers: { main: { id: A, file: transcript(A) } },
				steps: [
					...everyTrigger(MAIN, "main"),
					{ set: "main", id: B, file: transcript(B) },
					...everyTrigger(MAIN, "main"),
					{ sleep: 300 },
				],
			},
			{ runner, intervalMs: 50 },
		);
		expect(snapshots.filter((s) => s !== null)).toEqual([]);
		expect(saved).toEqual([]);
		expect(readdirSync(runtime)).toEqual([]);
	}, 15_000);

	test.each([
		["a subagent's", SUBAGENT, SUB],
		["an advisor's", ADVISING, ADVISOR],
	] as const)("%s events, and the timer after them, leave the main session's file as it was", async (_name, agent, id) => {
		const { pid, snapshots, saved } = await run(
			{
				managers: { main: { id: A, file: transcript(A) }, other: { id, file: transcript(id) } },
				steps: [{ event: "session_start", agent: MAIN, manager: "main" }, ...everyTrigger(agent, "other"), { sleep: 300 }],
			},
			{ intervalMs: 50 },
		);
		expect(snapshots.map((s) => s?.content)).toEqual(snapshots.map(() => reportOf(pid, A, transcript(A))));
		expect(snapshots.map((s) => s?.ino)).toEqual(snapshots.map(() => snapshots[0]?.ino));
		expect(saved).toEqual([{ manager: "main", id: A, held: null }]);
	}, 15_000);

	test.each([
		["turn_end", { event: "turn_end", agent: MAIN, manager: "main" }, HOUR],
		["tool_call", { event: "tool_call", agent: MAIN, manager: "main" }, HOUR],
		["the timer", { sleep: 400 }, 50],
	] as const)("%s rewrites the file when the session's id or path changed with no event, and only then", async (_name, trigger, intervalMs) => {
		const moved = path.join(home, "moved", `2026-11-15T10-00-00-000Z_${B}.jsonl`);
		const { pid, snapshots, saved } = await run(
			{
				managers: { main: { id: A, file: transcript(A) } },
				steps: [
					{ event: "session_start", agent: MAIN, manager: "main" },
					trigger,
					// omp's silent change of id: no event says so.
					{ set: "main", id: B, file: transcript(B) },
					trigger,
					// A /move: the same session, its transcript somewhere else.
					{ set: "main", id: B, file: moved },
					trigger,
					trigger,
					// An id that changes while the path stays, which the report must follow too.
					{ set: "main", id: C, file: moved },
					trigger,
				],
			},
			{ intervalMs },
		);
		expect(snapshots.map((s) => s?.content)).toEqual([
			reportOf(pid, A, transcript(A)),
			reportOf(pid, A, transcript(A)),
			reportOf(pid, A, transcript(A)),
			reportOf(pid, B, transcript(B)),
			reportOf(pid, B, transcript(B)),
			reportOf(pid, B, moved),
			reportOf(pid, B, moved),
			reportOf(pid, B, moved),
			reportOf(pid, C, moved),
		]);
		// Unchanged, nothing is written or saved.
		expect(snapshots[1]?.ino).toBe(snapshots[0]?.ino);
		expect(snapshots[6]?.ino).toBe(snapshots[5]?.ino);
		expect(saved).toEqual([
			{ manager: "main", id: A, held: null },
			{ manager: "main", id: B, held: reportOf(pid, A, transcript(A)) },
			{ manager: "main", id: B, held: reportOf(pid, B, transcript(B)) },
			{ manager: "main", id: C, held: reportOf(pid, B, moved) },
		]);
	}, 15_000);

	test("a session with no transcript (--no-session) writes no session file", async () => {
		const { snapshots } = await run({ managers: { main: { id: A } }, steps: everyTrigger(MAIN, "main") });
		expect(snapshots).toEqual([null, null, null, null, null]);
	});
});
