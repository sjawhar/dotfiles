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
const OTHER = "01a1a3c0-4d5e-7f60-8a1b-2c3d4e5f6071";

let home = "";
let calls = "";
let fakeBin = "";
let transcript = "";
let otherTranscript = "";
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
	// is a failure. `register --session-id <id>` waits FAKE_REGISTER_DELAY seconds, then answers
	// as the helper does for one process: it prints FAKE_REGISTER_ERR on stderr and exits 1 when
	// that is set, for the id FAKE_REFUSE_ID or for every id; it refuses an id other than the one
	// it took first; and it takes the first id it is not told to refuse.
	writeFileSync(
		path.join(fakeBin, "agent-secrets"),
		[
			"#!/bin/sh",
			`printf '%s\\n' "$*" >> '${calls}'`,
			'case "$1" in',
			'identity) exit "${FAKE_IDENTITY_EXIT:-0}" ;;',
			"register)",
			'	sleep "${FAKE_REGISTER_DELAY:-0}"',
			'	if [ -n "$FAKE_REGISTER_ERR" ] && [ "${FAKE_REFUSE_ID:-$3}" = "$3" ]; then printf \'%s\\n\' "$FAKE_REGISTER_ERR" >&2; exit 1; fi',
			`	named="$(cat '${home}/named' 2>/dev/null)"`,
			'	if [ -n "$named" ] && [ "$named" != "$3" ]; then',
			"		printf 'agent-secrets register: BAD_REQUEST: this process is in session %s, which cannot become session %s\\n' \"$named\" \"$3\" >&2",
			"		exit 1",
			"	fi",
			`	printf '%s\\n' "$3" > '${home}/named'`,
			"	;;",
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
	otherTranscript = path.join(bucket, `2026-11-04T01-02-03-004Z_${OTHER}.jsonl`);
	writeFileSync(otherTranscript, "");
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
/**
 * `starts` session_start events, then, `pauseMs` later, a session_switch to each of `switches`,
 * in one fresh process; resolves once that process has exited.
 */
async function start(
	ctx: { kind: "main" | "sub"; file: string; starts?: number; pauseMs?: number; switches?: string[] },
	extraEnv: Record<string, string>,
): Promise<Started> {
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
		// The fake answers in milliseconds, so a second is long after it has.
		["after the register returned", { pauseMs: 1000 }, "0"],
		["while the register runs", {}, "1"],
	] as const)("keepsTheIdItNamedAcrossASessionSwitch: %s", async (_name, pause, delay) => {
		// The helper refuses a process it has named any other id, so a second register only
		// writes a refusal; the process keeps the broker identity it has, under ROOT.
		const sock = await listen(path.join(home, "h.sock"));
		const started = await start(
			{ kind: "main", file: transcript, ...pause, switches: [otherTranscript] },
			{ AGENT_SECRETS_HELPER_SOCK: sock, FAKE_REGISTER_DELAY: delay },
		);
		expect(started.id).toBe(OTHER);
		expect(logged()).toEqual([`register --session-id ${ROOT}`]);
		expect(started.stderr.split("\n").filter(line => line.includes("session-env:"))).toEqual([]);
	});

	test("namesTheNextIdWhenTheFirstWasRefused", async () => {
		const sock = await listen(path.join(home, "h.sock"));
		const refusal = "agent-secrets register: helper unreachable";
		const started = await start(
			{ kind: "main", file: transcript, switches: [otherTranscript] },
			{ AGENT_SECRETS_HELPER_SOCK: sock, FAKE_REGISTER_ERR: refusal, FAKE_REFUSE_ID: ROOT },
		);
		expect(started.id).toBe(OTHER);
		expect(logged()).toEqual([`register --session-id ${ROOT}`, `register --session-id ${OTHER}`]);
		const lines = started.stderr.split("\n").filter(line => line.includes("session-env:"));
		expect(lines).toHaveLength(1);
		expect(lines[0]).toContain(`agent-secrets register --session-id ${ROOT} failed`);
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
			{ AGENT_SECRETS_HELPER_SOCK: sock, FAKE_REGISTER_ERR: refusal },
		);
		expect(started.id).toBe(ROOT);
		const lines = started.stderr.split("\n").filter(line => line.includes("session-env:"));
		expect(lines).toHaveLength(1);
		expect(lines[0]).toContain(`agent-secrets register --session-id ${ROOT}`);
		expect(lines[0]).toContain(`~/.dotfiles/shims/omp --resume ${ROOT}`);
		expect(lines[0]).toContain(refusal);
	});
});
