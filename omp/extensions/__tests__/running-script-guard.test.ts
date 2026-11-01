import { afterEach, describe, expect, mock, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

mock.module("@oh-my-pi/pi-natives", () => ({ editInspect: () => ({ paths: [] }) }));
// The bound on a wait for a /proc walk; this box's walks can take seconds under load.
process.env.OMP_RUNNING_SCRIPT_GUARD_MS = "60000";
// Loaded after the mock and the bound: the guard imports pi-natives, which `bun test` does not
// resolve outside omp, and reads the bound when it loads.
const { createRunningScriptGuard } = await import("../running-script-guard");

type Handler = (event: unknown, ctx: unknown) => unknown;
type Content = Array<{ type: string; text?: string }>;

// The run pauses on a fifo after line 3; the 4,000 lines after it are well past what bash reads from
// its script at a time, so it must read the file again after any write made during the pause.
const TAIL = Array.from({ length: 4_000 }, (_, i) => `echo line-${i}`);
const ORIGINAL = ["#!/bin/bash", "echo started", 'read -r _ < "$1"', ...TAIL, "echo AFTER-ORIGINAL", ""].join("\n");
const AS_WRITTEN = ["started", ...TAIL.map(line => line.slice(5)), "AFTER-ORIGINAL", ""].join("\n");
const NEW = "#!/bin/bash\n# written while the old version ran: padding padding padding padding\necho AFTER-NEW\n";

const roots: string[] = [];
const victims: Array<{ kill(): void; exitCode: number | null }> = [];
afterEach(() => {
	// A test that failed before releasing its run leaves bash blocked on the fifo.
	for (const proc of victims.splice(0)) if (proc.exitCode === null) proc.kill();
	// rm needs only the directory writable, so a script left read-only goes too.
	for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

/** A fresh directory with new.sh (NEW). */
function workdir(): string {
	const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "running-script-guard-")));
	roots.push(dir);
	fs.writeFileSync(path.join(dir, "new.sh"), NEW);
	return dir;
}

/**
 * victim.sh, run by bash until `finish` releases it. `links` gives the file a second name. The
 * shell carries no OMPCODE=1: the guards of omp sessions this box started before the fix still act
 * on every shell that does, and would change these scripts in passes of their own.
 */
async function run(dir: string, opts: { links?: boolean } = {}) {
	const script = path.join(dir, "victim.sh");
	fs.writeFileSync(script, ORIGINAL);
	fs.chmodSync(script, 0o755);
	if (opts.links) fs.linkSync(script, path.join(dir, "victim.link"));
	const fifo = path.join(dir, "go.fifo");
	expect(Bun.spawnSync(["mkfifo", fifo]).exitCode).toBe(0);
	const env: Record<string, string | undefined> = { ...process.env };
	delete env.OMPCODE;
	const proc = Bun.spawn(["bash", "victim.sh", fifo], { cwd: dir, env, stdout: "pipe", stderr: "pipe" });
	victims.push(proc);
	const reader = proc.stdout.getReader();
	const decoder = new TextDecoder();
	let out = "";
	// "started" is printed after bash opened its script and before it blocks on the fifo.
	while (!out.includes("started\n")) {
		const { value, done } = await reader.read();
		if (done) throw new Error(`victim exited early: ${out}`);
		out += decoder.decode(value);
	}
	return {
		pid: proc.pid,
		script,
		/** Lets the run go on, and returns everything it printed, stderr after stdout. */
		async finish(): Promise<string> {
			fs.writeFileSync(fifo, "go\n");
			for (;;) {
				const { value, done } = await reader.read();
				if (done) break;
				out += decoder.decode(value);
			}
			const err = await new Response(proc.stderr).text();
			await proc.exited;
			return out + err;
		},
	};
}

/** The guard on a fake `pi`, its records in `root`/state; each tool call waits for a whole pass. */
function guard(root: string, opts: { passIntervalMs?: number } = {}) {
	const handlers = new Map<string, Handler>();
	const warnings: string[] = [];
	const pi = { on: (event: string, h: Handler) => handlers.set(event, h), logger: { debug() {}, warn: (message: string) => void warnings.push(message) } };
	createRunningScriptGuard({ stateDir: path.join(root, "state"), passIntervalMs: opts.passIntervalMs ?? 0, passWaitMs: 60_000 })(pi as never);
	const toolCall = (toolName: string, input: Record<string, unknown>, cwd = root) =>
		handlers.get("tool_call")?.({ type: "tool_call", toolName, toolCallId: "t1", input }, { cwd }) as Promise<{ block?: boolean; reason?: string } | undefined>;
	return {
		warnings,
		toolCall,
		/** A bash tool call as omp runs it: the guard's tool_call handler, then the command. */
		async bash(command: string, cwd = root) {
			const verdict = await toolCall("bash", { command }, cwd);
			if (verdict?.block) return { exitCode: -1, stderr: verdict.reason ?? "" };
			const done = Bun.spawnSync(["bash", "-c", `exec </dev/null\n${command}`], { cwd, timeout: 20_000 });
			return { exitCode: done.exitCode, stderr: done.stderr.toString() };
		},
		toolResult: (text: string, cwd = root) =>
			handlers.get("tool_result")?.({ type: "tool_result", toolName: "bash", toolCallId: "t1", input: {}, content: [{ type: "text", text }], isError: true }, { cwd }) as Promise<{ content?: Content } | undefined>,
		shutdown: () => handlers.get("session_shutdown")?.({ type: "session_shutdown" }, { cwd: root }) as Promise<void>,
	};
}

const modeOf = (file: string) => fs.statSync(file).mode & 0o7777;

/**
 * The record the guards of 2026-10-31 (005cb5b, every pass) and 2026-11-01 (ac7706c to 34cbf94,
 * fallback) wrote in `held/` when they took a running script's write bits, with the bits taken.
 */
function olderGuardTook(stateDir: string, victim: { pid: number; script: string }): void {
	const st = fs.statSync(victim.script);
	const mode = st.mode & 0o7777;
	const record = { path: victim.script, dev: st.dev, ino: st.ino, mode, reduced: mode & ~0o222, at: Date.now(), pid: victim.pid, cmdline: "bash victim.sh" };
	fs.mkdirSync(path.join(stateDir, "held"), { recursive: true });
	fs.writeFileSync(path.join(stateDir, "held", `${Bun.hash(victim.script).toString(16)}.json`), JSON.stringify(record));
	fs.chmodSync(victim.script, record.reduced);
}

// 2026-11-01: guard passes of omp processes outside another lane's process tree took the write bits
// of the driver.sh its test harness had written in a fresh mkdtemp directory and was running
// (0644 -> 0444, the same inode); the bits outlived the run and the harness's next write failed with
// PermissionError. Measured from a third lane's scratch, 5 of 24 runs flipped and 0 of 24 on a
// never-run decoy with the same bytes beside it: the selector (any shell carrying the inherited
// OMPCODE=1), not the path, chose the file. The same day a pass put scripts/agent-session's bytes
// at shims/omp and shims/knives's at shims/gh. None of these files was written by omp.
describe("a script another session's shell is running", () => {
	test.each([
		["the only name of its file", false],
		["one of two names of its file", true],
	])("is refused to omp's file tools, and a pass leaves its mode, file and bytes as they are, when the path is %s", async (_name, links) => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir, { links });
		const before = fs.statSync(victim.script);
		const verdict = await g.toolCall("write", { path: "victim.sh", content: NEW }, dir);
		expect(verdict?.block).toBe(true);
		expect(verdict?.reason).toContain(`pid ${victim.pid}`);
		expect(verdict?.reason).toContain("write the new version to another name and `mv` it over victim.sh (a rename leaves the running copy reading the old file)");
		await g.bash("true", dir);
		const after = fs.statSync(victim.script);
		expect(after.mode & 0o7777).toBe(0o755);
		expect(after.ino).toBe(before.ino);
		expect(fs.readFileSync(victim.script, "utf8")).toBe(ORIGINAL);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);
});

// The pre-fix guards keep running in every omp process started before the fix, and their records
// stay behind when such a process is killed. Every pass of this guard gives their bits back.
describe("write bits an older guard took", () => {
	test("come back on the same file at the next pass, while the shell still runs it", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		const before = fs.statSync(victim.script);
		olderGuardTook(path.join(dir, "state"), victim);
		await g.bash("true", dir);
		const after = fs.statSync(victim.script);
		expect(after.mode & 0o7777).toBe(0o755);
		expect(after.ino).toBe(before.ino);
		expect(fs.readdirSync(path.join(dir, "state", "held"))).toEqual([]);
		expect((await g.bash("cat new.sh > victim.sh", dir)).exitCode).toBe(0);
		await victim.finish();
	}, 60_000);

	test("come back at session end, though a pass ran within the interval", async () => {
		const dir = workdir();
		const g = guard(dir, { passIntervalMs: 600_000 });
		const victim = await run(dir);
		await g.bash("true", dir); // the pass whose stamp is fresh when the session ends
		olderGuardTook(path.join(dir, "state"), victim);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555); // within the interval: no pass
		await g.shutdown();
		expect(modeOf(victim.script)).toBe(0o755);
		await victim.finish();
	}, 60_000);

	test("go to a copy sed -i made with the reduced mode, and the guard says so", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		olderGuardTook(path.join(dir, "state"), victim);
		expect(Bun.spawnSync(["sed", "-i", "s/AFTER-ORIGINAL/AFTER-RENAMED/", "victim.sh"], { cwd: dir }).exitCode).toBe(0);
		expect(modeOf(victim.script)).toBe(0o555); // sed copied the mode it found
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o755);
		const said = `gave back the write bits of ${victim.script} to a copy a rename writer made after the guard took them: 0555 -> 0755`;
		expect(g.warnings.join("\n")).toContain(said);
		expect(fs.readFileSync(path.join(dir, "state", "guard.log"), "utf8")).toContain(said);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("stay off such a copy once the record for it is gone", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		olderGuardTook(path.join(dir, "state"), victim);
		expect(Bun.spawnSync(["sed", "-i", "s/AFTER-ORIGINAL/AFTER-RENAMED/", "victim.sh"], { cwd: dir }).exitCode).toBe(0);
		fs.rmSync(path.join(dir, "state", "held"), { recursive: true });
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555);
		expect(g.warnings).toEqual([]);
		await victim.finish();
	}, 60_000);

	test("stay as someone else set them meanwhile", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		olderGuardTook(path.join(dir, "state"), victim);
		fs.chmodSync(victim.script, 0o700);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o700);
		await victim.finish();
	}, 60_000);

	test("come back through running-script-guard-restore with no omp session, which reports each file", async () => {
		const dir = workdir();
		const stateHome = path.join(dir, "xdg-state");
		const victim = await run(dir);
		olderGuardTook(path.join(stateHome, "omp-running-script-guard", "pid-test"), victim);
		const restore = path.join(import.meta.dir, "..", "..", "..", "scripts", "running-script-guard-restore");
		const done = Bun.spawnSync([restore], { env: { ...process.env, XDG_STATE_HOME: stateHome } });
		expect(done.exitCode).toBe(0);
		expect(done.stdout.toString()).toBe(`${victim.script}: gave back the write bits: 0555 -> 0755\n`);
		expect(modeOf(victim.script)).toBe(0o755);
		expect(fs.readdirSync(path.join(stateHome, "omp-running-script-guard", "pid-test", "held"))).toEqual([]);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("a call that fails on such a file is told why and what to do", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		olderGuardTook(path.join(dir, "state"), victim);
		const told = await g.toolResult("bash: line 1: victim.sh: Permission denied");
		const note = told?.content?.at(-1)?.text ?? "";
		expect(note).toContain(`${dir}/victim.sh is read-only because an older running-script guard`);
		expect(note).toContain(`pid ${victim.pid}`);
		expect(note).toContain("`mv` it over victim.sh");
		expect(await g.toolResult("bash: line 1: other.sh: Permission denied")).toBeUndefined();
		expect(await g.toolResult("victim.sh: No such file or directory")).toBeUndefined();
		await victim.finish();
	}, 60_000);
});
