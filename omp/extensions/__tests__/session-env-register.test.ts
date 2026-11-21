// A host omp session names itself to the agent-secrets helper (dispatch://INFRA-3848), so that a
// restart of the helper keeps the session's broker identity. Each test runs session_start in a
// fresh process (session-env-driver.ts) with a fake `agent-secrets` first on PATH, which logs
// each call's argv, and a listening Unix socket in place of the helper's. No test reaches this
// machine's real helper: the child's PATH, HOME and socket are the test's own.
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:net";
import * as os from "node:os";
import * as path from "node:path";

const ROOT = "01a1a3bd-26a0-72f1-9e6e-a7a1f6ee18fe";
const OWNER = `2026-11-03T23-27-24-832Z_${ROOT}`;

let home = "";
let calls = "";
let fakeBin = "";
let transcript = "";
const servers: Server[] = [];

/** A Unix socket listening at `file`, as the helper's would be. */
async function listen(file: string): Promise<string> {
	mkdirSync(path.dirname(file), { recursive: true });
	const server = createServer();
	servers.push(server);
	const { promise, resolve } = Promise.withResolvers<void>();
	server.listen(file, resolve);
	await promise;
	return file;
}

beforeEach(() => {
	// A short prefix: a Unix socket path holds at most 107 bytes.
	home = mkdtempSync(path.join(os.tmpdir(), "se-reg-"));
	calls = path.join(home, "agent-secrets.calls");
	fakeBin = path.join(home, "bin");
	mkdirSync(fakeBin);
	// One line per call. `identity` exits FAKE_IDENTITY_EXIT (0 by default: the caller is in a
	// session the helper registered); session-env never asks it, so an `identity` line in the log
	// is a failure. `register` prints FAKE_REGISTER_ERR on stderr and exits FAKE_REGISTER_EXIT.
	writeFileSync(
		path.join(fakeBin, "agent-secrets"),
		[
			"#!/bin/sh",
			`printf '%s\\n' "$*" >> '${calls}'`,
			'case "$1" in',
			'identity) exit "${FAKE_IDENTITY_EXIT:-0}" ;;',
			'register) [ -n "$FAKE_REGISTER_ERR" ] && printf \'%s\\n\' "$FAKE_REGISTER_ERR" >&2; exit "${FAKE_REGISTER_EXIT:-0}" ;;',
			"esac",
			"",
		].join("\n"),
	);
	writeFileSync(path.join(fakeBin, "jj"), "#!/bin/sh\necho /u/default.toml\n");
	chmodSync(path.join(fakeBin, "agent-secrets"), 0o755);
	chmodSync(path.join(fakeBin, "jj"), 0o755);
	const bucket = path.join(home, ".omp", "agent", "sessions", "--work--");
	mkdirSync(path.join(bucket, OWNER), { recursive: true });
	transcript = path.join(bucket, `${OWNER}.jsonl`);
	writeFileSync(transcript, "");
});

afterEach(async () => {
	for (const server of servers.splice(0)) {
		const { promise, resolve } = Promise.withResolvers<void>();
		server.close(() => resolve());
		await promise;
	}
	rmSync(home, { recursive: true, force: true });
});

type Started = { id: string | null; stderr: string };
/** `starts` session_start events in one fresh process; resolves once that process has exited. */
async function start(ctx: { kind: "main" | "sub"; file: string; starts?: number }, extraEnv: Record<string, string>): Promise<Started> {
	const env: Record<string, string | undefined> = { ...process.env, HOME: home, PATH: `${fakeBin}:${process.env.PATH ?? ""}` };
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
	env.XDG_RUNTIME_DIR = path.join(home, "run");
	Object.assign(env, extraEnv);
	const child = Bun.spawn([process.execPath, path.join(import.meta.dir, "session-env-driver.ts"), JSON.stringify(ctx)], {
		env,
		stdout: "pipe",
		stderr: "pipe",
	});
	const [out, err, code] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
	if (code !== 0) throw new Error(`driver exited ${code}: ${err}`);
	const exported = JSON.parse(out) as { id: string | null; said: string };
	return { id: exported.id, stderr: [exported.said, err].join("\n") };
}

/** The argv of each `agent-secrets` call, in order. */
function logged(): string[] {
	return existsSync(calls) ? readFileSync(calls, "utf8").split("\n").filter(Boolean) : [];
}

describe("a host omp session names itself to the secrets helper", () => {
	test("namesTheSessionToTheHelperOnce", async () => {
		const sock = await listen(path.join(home, "h.sock"));
		const started = await start({ kind: "main", file: transcript, starts: 2 }, { AGENT_SECRETS_HELPER_SOCK: sock });
		expect(logged()).toEqual([`register --session-id ${ROOT}`]);
		expect(started.id).toBe(ROOT);
	});

	test("finds the helper's default socket under XDG_RUNTIME_DIR, as the launcher does", async () => {
		await listen(path.join(home, "run", "agent-secrets", "helper.sock"));
		await start({ kind: "main", file: transcript }, {});
		expect(logged()).toEqual([`register --session-id ${ROOT}`]);
	});

	test("namesASessionTheLauncherDidNotRegister", async () => {
		// identity exits 1: no launcher registered this omp (one resumed without the shims), so it
		// has no broker identity yet. Since cybermonklabs/legion#4151 the register attaches the omp
		// above it, this process, so the session still names itself.
		const sock = await listen(path.join(home, "h.sock"));
		const started = await start({ kind: "main", file: transcript }, { AGENT_SECRETS_HELPER_SOCK: sock, FAKE_IDENTITY_EXIT: "1" });
		expect(logged()).toEqual([`register --session-id ${ROOT}`]);
		expect(started.id).toBe(ROOT);
	});

	test.each([
		["in a box", "main", async () => ({ AGENT_SECRETS_HELPER_SOCK: await listen(path.join(home, "h.sock")), AGENTBOX_BOX: "agentbox-0123456789ab" })],
		["for a subagent", "sub", async () => ({ AGENT_SECRETS_HELPER_SOCK: await listen(path.join(home, "h.sock")) })],
		["with no socket", "main", async () => ({ AGENT_SECRETS_HELPER_SOCK: path.join(home, "absent.sock") })],
		[
			"with a plain file where the socket would be",
			"main",
			async () => {
				writeFileSync(path.join(home, "file.sock"), "");
				return { AGENT_SECRETS_HELPER_SOCK: path.join(home, "file.sock") };
			},
		],
	] as const)("doesNothingInABoxForASubagentOrWithNoSocket: %s", async (_name, kind, env) => {
		await start({ kind, file: transcript }, await env());
		expect(logged()).toEqual([]);
	});

	test("namesOnlyTheSessionItsOwnTranscriptIs", async () => {
		// A subagent transcript resumed on its own commits as the session it belongs to, but it
		// runs in a process of its own: naming it would take that session's broker identity, and
		// its grants, from another process.
		const sock = await listen(path.join(home, "h.sock"));
		const started = await start({ kind: "main", file: path.join(path.dirname(transcript), OWNER, "Probe.jsonl") }, { AGENT_SECRETS_HELPER_SOCK: sock });
		expect(started.id).toBe(ROOT);
		expect(logged()).toEqual([]);
	});

	test.each([
		["a session of another name", `agent-secrets register: BAD_REQUEST: this process is in session 01a1a3b9-f15e-7307-af39-d0e9f1e3a3b4, which cannot become session ${ROOT}`],
		[
			"no omp above the call",
			"agent-secrets register: NOT_A_SESSION: pid 4242 is in no registered session and no session launcher (omp) is an ancestor of it, so a session registered for it would end with this command; relaunch the session under agent-secrets register --exec -- omp …",
		],
	] as const)("saysWhatToRunWhenTheHelperRefusesTheName: %s", async (_name, refusal) => {
		const sock = await listen(path.join(home, "h.sock"));
		const started = await start(
			{ kind: "main", file: transcript },
			{ AGENT_SECRETS_HELPER_SOCK: sock, FAKE_REGISTER_EXIT: "1", FAKE_REGISTER_ERR: refusal },
		);
		expect(started.id).toBe(ROOT);
		const lines = started.stderr.split("\n").filter(line => line.includes("session-env:"));
		expect(lines).toHaveLength(1);
		expect(lines[0]).toContain(`agent-secrets register --session-id ${ROOT}`);
		expect(lines[0]).toContain(`~/.dotfiles/shims/omp --resume ${ROOT}`);
		expect(lines[0]).toContain(refusal);
	});
});
