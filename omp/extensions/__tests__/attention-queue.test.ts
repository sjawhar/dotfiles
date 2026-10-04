import { beforeAll, describe, expect, test } from "bun:test";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type attentionQueue from "../attention-queue";

type Extension = typeof attentionQueue;
type Pi = Parameters<Extension>[0];
type Kind = "main" | "sub";
type Result = { code: number; stdout: string; stderr: string };
/** Runs one call, `setsid -w <script> <verb> …`; `verb` is the tmux-attention verb. */
type Exec = (command: string, args: string[], verb: string) => Promise<Result>;

const TMUX_ATTENTION = path.resolve(import.meta.dir, "../../../scripts/tmux-attention");
const PANE = "%42";
const SERVER = "4242";

// The extension reads TMUX, TMUX_PANE and TMUX_ATTENTION_SCRIPT once, when the
// module loads, and a subagent inherits its parent's. A static import is
// evaluated before this file's body can set them, so the module is imported
// here with a pane and this checkout's script in place, and the environment is
// put back once the module holds its copy.
let extension: Extension;
beforeAll(async () => {
	const saved = {
		TMUX: process.env.TMUX,
		TMUX_PANE: process.env.TMUX_PANE,
		TMUX_ATTENTION_SCRIPT: process.env.TMUX_ATTENTION_SCRIPT,
	};
	process.env.TMUX = `/tmp/tmux-test/default,${SERVER},0`;
	process.env.TMUX_PANE = PANE;
	process.env.TMUX_ATTENTION_SCRIPT = TMUX_ATTENTION;
	try {
		extension = (await import("../attention-queue")).default;
	} finally {
		for (const [name, value] of Object.entries(saved)) {
			if (value === undefined) delete process.env[name];
			else process.env[name] = value;
		}
	}
});

/** What the host reports about itself: `omp` in a terminal, `omp -p`, or `omp --mode rpc`. */
type Host = { mode: string; hasUI: boolean };
const INTERACTIVE: Host = { mode: "tui", hasUI: true };

const context = (kind: Kind, notes: string[] = [], host: Host = INTERACTIVE) => ({
	agent: kind === "main" ? { kind, id: "Main", name: "main", depth: 0 } : { kind, id: "0-Task", name: "task", depth: 1, parentId: "Main" },
	cwd: "/work/repo",
	...host,
	hasPendingMessages: () => false,
	sessionManager: { getSessionId: () => `${kind}-session`, getSessionName: () => undefined },
	ui: { notify: (message: string) => void notes.push(message), onTerminalInput: () => () => undefined },
});

/** One session binding of the extension, driven through a fake `pi` that records every tmux-attention call. */
function bind(run: Exec = async () => ({ code: 0, stdout: "", stderr: "" }), host: Host = INTERACTIVE) {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
	const calls: string[][] = [];
	const notes: string[] = [];
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => unknown) => handlers.set(event, handler),
		exec: (command: string, args: string[]) => {
			const call = args.slice(2);
			calls.push(call);
			return run(command, args, call[0]);
		},
	} as unknown as Pi;
	extension(pi);
	return {
		calls,
		notes,
		agentEnd: (kind: Kind) =>
			handlers.get("agent_end")?.({ messages: [{ role: "assistant", stopReason: "stop" }] }, context(kind, notes, host)),
		shutdown: (kind: Kind) => handlers.get("session_shutdown")?.({}, context(kind, notes, host)) as Promise<void>,
		askStart: (kind: Kind) => handlers.get("tool_execution_start")?.({ toolName: "ask" }, context(kind, notes, host)),
		askEnd: (kind: Kind) => handlers.get("tool_execution_end")?.({ toolName: "ask" }, context(kind, notes, host)),
	};
}

/** Run `command` for real, on a queue in `dir`. */
const onQueue = (dir: string, command: string, args: string[]): Promise<Result> => {
	const { promise, resolve } = Promise.withResolvers<Result>();
	execFile(command, args, { env: { ...process.env, OMP_ATTENTION_DIR: dir } }, (error, stdout, stderr) => {
		resolve({ code: error ? Number(error.code ?? 1) : 0, stdout, stderr });
	});
	return promise;
};

const queued = (dir: string): string[] =>
	readFileSync(path.join(dir, "queue.jsonl"), "utf8").split("\n").filter(Boolean);

describe("attention-queue", () => {
	test("a subagent's turn ending leaves the queue alone, though it carries the parent's pane", () => {
		const session = bind();
		session.agentEnd("sub");
		expect(session.calls).toEqual([]);
	});

	// The host's own report, as omp gives it: `-p` is mode "print" without a UI,
	// `--mode rpc` is mode "rpc" with one.
	test.each([
		{ mode: "print", hasUI: false },
		{ mode: "rpc", hasUI: true },
	])("a $mode-mode session queues nothing and drops nothing: nobody answers its pane", async (host) => {
		const session = bind(undefined, host);
		session.askStart("main");
		session.askEnd("main");
		session.agentEnd("main");
		await session.shutdown("main");
		expect(session.calls).toEqual([]);
	});

	test("an interactive session queues its pane when a turn ends and drops it at shutdown", async () => {
		const session = bind();
		session.agentEnd("main");
		await session.shutdown("main");
		expect(session.calls.map(([verb]) => verb)).toEqual(["push", "drop"]);
	});

	test("the top-level session's turn ending queues its pane", () => {
		const session = bind();
		session.agentEnd("main");
		expect(session.calls).toHaveLength(1);
		const [verb, line] = session.calls[0];
		expect(verb).toBe("push");
		expect(JSON.parse(line)).toMatchObject({ pane: PANE, server: SERVER, session: "main-session", title: "repo" });
	});

	test("the shutdown drop starts at once behind a held lock, and the queue still ends empty", async () => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "attention-queue-"));
		// Another session's call holding the queue lock, as under a burst of pushes.
		// It keeps the lock until its stdin closes.
		const holder = spawn("flock", [path.join(dir, "queue.lock"), "sh", "-c", "echo held; cat >/dev/null"], {
			stdio: ["pipe", "pipe", "ignore"],
		});
		try {
			await once(holder.stdout, "data");
			const running: Promise<Result>[] = [];
			const session = bind((command, args) => {
				const call = onQueue(dir, command, args);
				running.push(call);
				return call;
			});
			session.agentEnd("main");
			const dropped = session.shutdown("main");
			// omp gives a session_shutdown handler 2 s and then exits, and a call not
			// started by then never runs: the drop has to start while the handler runs.
			expect(session.calls.map(([verb]) => verb)).toEqual(["push", "drop"]);
			holder.stdin.end();
			await dropped;
			await Promise.all(running);
			expect(session.notes).toEqual([]);
			expect(queued(dir)).toEqual([]);
		} finally {
			holder.kill();
			rmSync(dir, { recursive: true, force: true });
		}
	});

	test("the shutdown drop outlives omp's process group, as when omp is the pane's own command", async () => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "attention-queue-"));
		const line = { pane: PANE, server: SERVER, session: "s", title: "t", cwd: "/work/repo", at: "2026-01-01T00:00:00Z" };
		await onQueue(dir, TMUX_ATTENTION, ["push", JSON.stringify(line), "1000"]);
		// Hold the lock so the drop is still waiting for it when the pane closes.
		const holder = spawn("flock", [path.join(dir, "queue.lock"), "sh", "-c", "echo held; cat >/dev/null"], {
			stdio: ["pipe", "pipe", "ignore"],
		});
		const omp = spawn("bun", [path.join(import.meta.dir, "shutdown-driver.ts")], {
			detached: true,
			stdio: ["ignore", "pipe", "inherit"],
			env: {
				...process.env,
				TMUX: `/tmp/tmux-test/default,${SERVER},0`,
				TMUX_PANE: PANE,
				TMUX_ATTENTION_SCRIPT: TMUX_ATTENTION,
				OMP_ATTENTION_DIR: dir,
			},
		});
		try {
			await once(holder.stdout, "data");
			await once(omp.stdout!, "data");
			// The pane closes: its process group gets the hangup, the drop's own call included.
			process.kill(-omp.pid!, "SIGHUP");
			await once(omp, "exit");
			holder.stdin.end();
			// The drop runs in its own session and gives this test nothing to await, so the
			// test watches the queue it rewrites, for as long as a loaded machine may need.
			const deadline = Date.now() + 30_000;
			while (queued(dir).length > 0 && Date.now() < deadline) await Bun.sleep(20);
			expect(queued(dir)).toEqual([]);
		} finally {
			holder.kill();
			rmSync(dir, { recursive: true, force: true });
		}
	}, 60_000);

	test("a push after a drop keeps its line when the drop reaches the lock last", async () => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "attention-queue-"));
		try {
			const { promise: gate, resolve: release } = Promise.withResolvers<void>();
			const running: Promise<Result>[] = [];
			const session = bind((command, args, verb) => {
				const call = (verb === "drop" ? gate : Promise.resolve()).then(() => onQueue(dir, command, args));
				running.push(call);
				return call;
			});
			session.askEnd("main");
			session.agentEnd("main");
			const [drop, push] = running;
			await push;
			release();
			await drop;
			expect(session.calls.map(([verb]) => verb)).toEqual(["drop", "push"]);
			expect(session.notes).toEqual([]);
			expect(queued(dir).map((line) => JSON.parse(line).pane)).toEqual([PANE]);
		} finally {
			rmSync(dir, { recursive: true, force: true });
		}
	});

	test("a push held back until after the shutdown drop is ignored, so the queue ends empty", async () => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "attention-queue-"));
		try {
			const { promise: gate, resolve: release } = Promise.withResolvers<void>();
			const running: Promise<Result>[] = [];
			const session = bind((command, args, verb) => {
				const call = (verb === "push" ? gate : Promise.resolve()).then(() => onQueue(dir, command, args));
				running.push(call);
				return call;
			});
			session.agentEnd("main");
			await session.shutdown("main");
			release();
			await Promise.all(running);
			expect(session.calls.map(([verb]) => verb)).toEqual(["push", "drop"]);
			expect(session.notes).toEqual([]);
			expect(queued(dir)).toEqual([]);
		} finally {
			rmSync(dir, { recursive: true, force: true });
		}
	});
});
