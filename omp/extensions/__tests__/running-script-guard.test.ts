import { afterEach, describe, expect, mock, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

mock.module("@oh-my-pi/pi-natives", () => ({ editInspect: () => ({ paths: [] }) }));
// The bound on a wait for a /proc walk; this box's walks can take seconds under load.
process.env.OMP_RUNNING_SCRIPT_GUARD_MS = "60000";
// Loaded after the mock and the bound: the guard imports pi-natives, which `bun test` does not
// resolve outside omp, and reads the bound when it loads.
const { createRunningScriptGuard, shellHeldFiles } = await import("../running-script-guard");

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

/** A fresh directory with victim.sh (ORIGINAL), new.sh (NEW) and d.patch (victim.sh's last line changed). */
function workdir(): string {
	const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "running-script-guard-")));
	roots.push(dir);
	fs.writeFileSync(path.join(dir, "new.sh"), NEW);
	fs.writeFileSync(path.join(dir, "orig.sh"), ORIGINAL);
	fs.writeFileSync(path.join(dir, "renamed.sh"), ORIGINAL.replace("AFTER-ORIGINAL", "AFTER-RENAMED"));
	Bun.spawnSync(["sh", "-c", "diff -u --label victim.sh --label victim.sh orig.sh renamed.sh > d.patch"], { cwd: dir });
	return dir;
}

/** victim.sh, run by bash until `finish` releases it; its environment carries omp's OMPCODE=1 unless `agent` is false. */
async function run(dir: string, opts: { agent?: boolean; mode?: number } = {}) {
	const script = path.join(dir, "victim.sh");
	fs.writeFileSync(script, ORIGINAL);
	fs.chmodSync(script, opts.mode ?? 0o755);
	const fifo = path.join(dir, "go.fifo");
	expect(Bun.spawnSync(["mkfifo", fifo]).exitCode).toBe(0);
	const env: Record<string, string | undefined> = { ...process.env, OMPCODE: "1" };
	if (opts.agent === false) delete env.OMPCODE;
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

/**
 * The guard on a fake `pi`, its records in `stateDir`, its passes limited to scripts under `root`.
 * Each tool call waits for a whole pass, unless `production` asks for the shipped timing.
 */
function guard(root: string, opts: { stateDir?: string; followUpMs?: number; production?: boolean } = {}) {
	const handlers = new Map<string, Handler>();
	const warnings: string[] = [];
	const pi = { on: (event: string, h: Handler) => handlers.set(event, h), logger: { debug() {}, warn: (message: string) => void warnings.push(message) } };
	createRunningScriptGuard({
		stateDir: opts.stateDir ?? path.join(root, "state"),
		passIntervalMs: opts.production ? 2_000 : 0,
		passWaitMs: opts.production ? 0 : 60_000,
		followUpMs: opts.followUpMs ?? 600_000,
		discover: async () => (await shellHeldFiles()).filter(h => h.file.startsWith(`${root}/`)),
	})(pi as never);
	const toolCall = (toolName: string, input: Record<string, unknown>, cwd = root) =>
		handlers.get("tool_call")?.({ type: "tool_call", toolName, toolCallId: "t1", input }, { cwd }) as Promise<{ block?: boolean; reason?: string } | undefined>;
	return {
		warnings,
		toolCall,
		/** A bash tool call as omp runs it: the guard's tool_call handler, then the command. */
		async bash(command: string, cwd = root) {
			const verdict = await toolCall("bash", { command }, cwd);
			if (verdict?.block) return { exitCode: -1, stderr: verdict.reason ?? "" };
			// stdin is /dev/null, as in omp's bash tool (Bun's own stdin options give a closed fd or an open pipe).
			const done = Bun.spawnSync(["bash", "-c", `exec </dev/null\n${command}`], { cwd, timeout: 20_000 });
			return { exitCode: done.exitCode, stderr: done.stderr.toString() };
		},
		toolResult: (text: string, cwd = root) =>
			handlers.get("tool_result")?.({ type: "tool_result", toolName: "bash", toolCallId: "t1", input: {}, content: [{ type: "text", text }], isError: true }, { cwd }) as Promise<{ content?: Content } | undefined>,
		shutdown: () => handlers.get("session_shutdown")?.({ type: "session_shutdown" }, { cwd: root }) as Promise<void>,
	};
}

const modeOf = (file: string) => fs.statSync(file).mode & 0o7777;

// The in-place writers of the 2026-10-31 measurement that every machine here has.
const IN_PLACE: Array<[string, string]> = [
	["python3 open(w) from a heredoc", "python3 - <<'EOF'\np = 'victim.sh'\nopen(p, 'w').write(open('new.sh').read())\nEOF"],
	["perl open(>)", `perl -e 'open(my $n, "<", "new.sh"); local $/; my $s = <$n>; open(my $f, ">", "victim.sh") or die "$!\\n"; print $f $s'`],
	["a shell redirect", "cat new.sh > victim.sh"],
	["a builtin's redirect", "printf 'echo AFTER-NEW\\n' > victim.sh"],
	["an append", "echo 'echo APPENDED' >> victim.sh"],
	["tee", "tee victim.sh < new.sh > /dev/null"],
	["cp onto it", "cp new.sh victim.sh"],
	["dd", "dd if=new.sh of=victim.sh status=none"],
	["truncate", "truncate -s 0 victim.sh"],
	["a script that writes the script", "printf 'cat new.sh > victim.sh\\n' > helper.sh && bash helper.sh"],
];

// The rename writers of the same measurement: each puts a new file at the path. A tool this machine
// lacks drops out of the table rather than passing unrun.
const RENAMES = (
	[
		["sed -i", "sed", "sed -i 's/AFTER-ORIGINAL/AFTER-RENAMED/' victim.sh"],
		["perl -i", "perl", "perl -i -pe 's/AFTER-ORIGINAL/AFTER-RENAMED/' victim.sh"],
		["gawk -i inplace", "gawk", `gawk -i inplace '{ sub(/AFTER-ORIGINAL/, "AFTER-RENAMED") } 1' victim.sh`],
		["yq -i", "yq", "yq -i -p props -o props '.' victim.sh"],
		["mv", "mv", "cp renamed.sh next.sh && mv next.sh victim.sh"],
		["cp --remove-destination", "cp", "cp --remove-destination renamed.sh victim.sh"],
		["install", "install", "install -m 755 renamed.sh victim.sh"],
		["rsync", "rsync", "rsync renamed.sh victim.sh"],
		["patch", "patch", "patch -s victim.sh < d.patch"],
		["python3 os.replace", "python3", "python3 -c \"import os; open('next.sh', 'w').write(open('renamed.sh').read()); os.replace('next.sh', 'victim.sh')\""],
	] as Array<[string, string, string]>
)
	.filter(([, tool]) => Bun.which(tool) !== null)
	.map(([name, , command]): [string, string] => [name, command]);

describe("a script an agent's shell is running", () => {
	test.each(IN_PLACE)(
		"a bash call that rewrites it in place with %s fails, and the run finishes as written",
		async (_name, command) => {
			const dir = workdir();
			const g = guard(dir);
			const victim = await run(dir);
			const call = await g.bash(command, dir);
			expect(call.exitCode).not.toBe(0);
			expect(fs.readFileSync(victim.script, "utf8")).toBe(ORIGINAL);
			expect(await victim.finish()).toBe(AS_WRITTEN);
		},
		60_000,
	);

	test.each(RENAMES)(
		"a bash call that replaces it with %s works, and the run still finishes as written",
		async (_name, command) => {
			const dir = workdir();
			const g = guard(dir);
			const victim = await run(dir);
			const call = await g.bash(command, dir);
			expect(call.stderr).toBe("");
			expect(call.exitCode).toBe(0);
			expect(fs.readFileSync(victim.script, "utf8")).not.toBe(ORIGINAL);
			expect(await victim.finish()).toBe(AS_WRITTEN);
		},
		60_000,
	);

	// ex reports the read-only file and exits 1 when its stdin is /dev/null, as in omp's bash tool. It
	// waits for more commands instead when stdin is closed, or when its output goes to a pipe Bun's
	// spawnSync reads, so the test sends ex's output to a file.
	test.skipIf(Bun.which("ex") === null)("an ex edit in a bash call fails at once, and the run finishes as written", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		const call = await g.bash("ex -u NONE -sc '%s/AFTER-ORIGINAL/AFTER-EX/|x' victim.sh > ex.out 2>&1", dir);
		expect(call.exitCode).toBe(1); // a wait would end at the bash call's 20 s timeout, with no exit code
		expect(fs.readFileSync(victim.script, "utf8")).toBe(ORIGINAL);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("with the shipped timing, a session's first call is protected before it runs", async () => {
		const dir = workdir();
		const g = guard(dir, { production: true });
		const victim = await run(dir);
		const call = await g.bash("python3 -c \"open('victim.sh', 'w').write('echo AFTER-NEW\\n')\"", dir);
		expect(call.exitCode).not.toBe(0);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("python's write from any other tool call, such as eval, fails the same way", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		await g.toolCall("eval", { cells: [{ language: "python", code: "open('victim.sh', 'w')" }] }, dir);
		const write = Bun.spawnSync(["python3", "-c", "open('victim.sh', 'w').write('echo AFTER-NEW\\n')"], { cwd: dir });
		expect(write.exitCode).not.toBe(0);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("an edit or write call that names it is still refused before it runs", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		const verdict = await g.toolCall("write", { path: "victim.sh", content: NEW }, dir);
		expect(verdict?.block).toBe(true);
		expect(verdict?.reason).toContain(`pid ${victim.pid}`);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("a call that fails on it is told why and what to do, and an unrelated failure is not", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		await g.bash("true", dir);
		const told = await g.toolResult("bash: line 1: victim.sh: Permission denied");
		const note = told?.content?.at(-1)?.text ?? "";
		expect(note).toContain(`${dir}/victim.sh is read-only while a shell runs it (pid ${victim.pid}`);
		expect(note).toContain("`mv` it over victim.sh");
		expect(await g.toolResult("bash: line 1: other.sh: Permission denied")).toBeUndefined();
		expect(await g.toolResult("victim.sh: No such file or directory")).toBeUndefined();
		await victim.finish();
	}, 60_000);
});

describe("giving the write bits back", () => {
	test("they come back once the shell exits, and a write in place then works", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555); // still running: still protected
		expect(await victim.finish()).toBe(AS_WRITTEN);
		expect((await g.bash("cat new.sh > victim.sh", dir)).exitCode).toBe(0);
		expect(modeOf(victim.script)).toBe(0o755);
		expect(fs.readFileSync(victim.script, "utf8")).toBe(NEW);
	}, 60_000);

	test("another session gives them back when the one that took them is gone", async () => {
		const dir = workdir();
		const state = path.join(dir, "state");
		const victim = await run(dir);
		await guard(dir, { stateDir: state }).bash("true", dir); // this session then dies: no shutdown, no later pass
		expect(modeOf(victim.script)).toBe(0o555);
		expect(await victim.finish()).toBe(AS_WRITTEN);
		await guard(dir, { stateDir: state }).bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o755);
	}, 60_000);

	test("a session that makes no further call gives them back on its own", async () => {
		const dir = workdir();
		const g = guard(dir, { followUpMs: 50 });
		const victim = await run(dir);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555);
		expect(await victim.finish()).toBe(AS_WRITTEN);
		// The follow-up pass runs on a real timer; wait for its effect, bounded.
		const deadline = Date.now() + 30_000;
		while (modeOf(victim.script) !== 0o755 && Date.now() < deadline) await Bun.sleep(50);
		expect(modeOf(victim.script)).toBe(0o755);
	}, 60_000);

	test("the last omp session to end gives them back, even for a script still running", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555);
		await g.shutdown();
		expect(modeOf(victim.script)).toBe(0o755);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("a session that ends while another lives leaves a running script protected", async () => {
		const dir = workdir();
		const state = path.join(dir, "state");
		const ending = guard(dir, { stateDir: state });
		const staying = guard(dir, { stateDir: state });
		const victim = await run(dir);
		await ending.bash("true", dir);
		await ending.shutdown();
		expect(modeOf(victim.script)).toBe(0o555);
		expect(await victim.finish()).toBe(AS_WRITTEN);
		await staying.shutdown();
		expect(modeOf(victim.script)).toBe(0o755);
	}, 60_000);

	test("a copy that sed -i made with the reduced mode gets them as soon as nothing runs it, and the guard says so", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		expect((await g.bash("sed -i 's/AFTER-ORIGINAL/AFTER-RENAMED/' victim.sh", dir)).exitCode).toBe(0);
		expect(modeOf(victim.script)).toBe(0o555); // sed copied the mode it found
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o755); // the old run goes on, on the old inode
		const said = `gave back the write bits of ${victim.script} to a copy a rename writer made after the guard took them: 0555 -> 0755`;
		expect(g.warnings.join("\n")).toContain(said);
		expect(fs.readFileSync(path.join(dir, "state", "guard.log"), "utf8")).toContain(said);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("such a copy keeps its mode once the guard's record for it is gone", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		expect((await g.bash("sed -i 's/AFTER-ORIGINAL/AFTER-RENAMED/' victim.sh", dir)).exitCode).toBe(0);
		fs.rmSync(path.join(dir, "state", "held"), { recursive: true });
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o555);
		expect(g.warnings).toEqual([]);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("running-script-guard-restore gives them back with no omp session, and reports each file", async () => {
		const dir = workdir();
		const stateHome = path.join(dir, "xdg-state");
		const g = guard(dir, { stateDir: path.join(stateHome, "omp-running-script-guard", "pid-test") });
		const victim = await run(dir);
		await g.bash("true", dir); // this session then dies: its records stay
		expect(modeOf(victim.script)).toBe(0o555);
		const restore = path.join(import.meta.dir, "..", "..", "..", "scripts", "running-script-guard-restore");
		const done = Bun.spawnSync([restore], { env: { ...process.env, XDG_STATE_HOME: stateHome } });
		expect(done.exitCode).toBe(0);
		expect(done.stdout.toString()).toBe(`${victim.script}: gave back the write bits: 0555 -> 0755\n`);
		expect(modeOf(victim.script)).toBe(0o755);
		expect(fs.readdirSync(path.join(stateHome, "omp-running-script-guard", "pid-test", "held"))).toEqual([]);
		expect(await victim.finish()).toBe(AS_WRITTEN);
	}, 60_000);

	test("a file its owner made read-only stays read-only", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir, { mode: 0o550 });
		await g.bash("true", dir);
		expect(await victim.finish()).toBe(AS_WRITTEN);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o550);
	}, 60_000);

	test("a mode someone else set meanwhile wins: the guard neither takes the bits again nor gives them back", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir);
		await g.bash("true", dir);
		fs.chmodSync(victim.script, 0o700);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o700);
		expect(await victim.finish()).toBe(AS_WRITTEN);
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o700);
	}, 60_000);

	test("a script a shell outside omp runs keeps its mode", async () => {
		const dir = workdir();
		const g = guard(dir);
		const victim = await run(dir, { agent: false });
		await g.bash("true", dir);
		expect(modeOf(victim.script)).toBe(0o755);
		await victim.finish();
	}, 60_000);
});
