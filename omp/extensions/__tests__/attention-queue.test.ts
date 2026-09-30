import { beforeAll, describe, expect, test } from "bun:test";
import type attentionQueue from "../attention-queue";

type Extension = typeof attentionQueue;
type Pi = Parameters<Extension>[0];
type Kind = "main" | "sub";

const PANE = "%42";
const SERVER = "4242";

// The extension reads TMUX and TMUX_PANE once, when the module loads, and a
// subagent inherits its parent's. A static import is evaluated before this
// file's body can set them, so the module is imported here with a pane in
// place, and the environment is put back once the module holds its copy.
let extension: Extension;
beforeAll(async () => {
	const saved = { TMUX: process.env.TMUX, TMUX_PANE: process.env.TMUX_PANE };
	process.env.TMUX = `/tmp/tmux-test/default,${SERVER},0`;
	process.env.TMUX_PANE = PANE;
	try {
		extension = (await import("../attention-queue")).default;
	} finally {
		for (const [name, value] of Object.entries(saved)) {
			if (value === undefined) delete process.env[name];
			else process.env[name] = value;
		}
	}
});

const context = (kind: Kind) => ({
	agent: kind === "main" ? { kind, id: "Main", name: "main", depth: 0 } : { kind, id: "0-Task", name: "task", depth: 1, parentId: "Main" },
	cwd: "/work/repo",
	hasPendingMessages: () => false,
	sessionManager: { getSessionId: () => `${kind}-session`, getSessionName: () => undefined },
	ui: { notify: () => undefined, onTerminalInput: () => () => undefined },
});

/** One session binding of the extension, driven through a fake `pi` that records every tmux-attention call. */
function bind() {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
	const calls: string[][] = [];
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => unknown) => handlers.set(event, handler),
		exec: async (_script: string, args: string[]) => {
			calls.push(args);
			return { code: 0, stdout: "", stderr: "" };
		},
	} as unknown as Pi;
	extension(pi);
	return {
		calls,
		agentEnd: (kind: Kind) =>
			handlers.get("agent_end")?.({ messages: [{ role: "assistant", stopReason: "stop" }] }, context(kind)),
	};
}

describe("attention-queue", () => {
	test("a subagent's turn ending leaves the queue alone, though it carries the parent's pane", () => {
		const session = bind();
		session.agentEnd("sub");
		expect(session.calls).toEqual([]);
	});

	test("the top-level session's turn ending queues its pane", () => {
		const session = bind();
		session.agentEnd("main");
		expect(session.calls).toHaveLength(1);
		const [verb, line] = session.calls[0];
		expect(verb).toBe("push");
		expect(JSON.parse(line)).toMatchObject({ pane: PANE, server: SERVER, session: "main-session", title: "repo" });
	});
});
