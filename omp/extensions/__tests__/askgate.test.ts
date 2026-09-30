import { afterEach, describe, expect, mock, test } from "bun:test";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import {
	ADVISOR_GATE_ENTRY_TYPE,
	breakerKey,
	type CompleteRequest,
	createAskGate,
	type Entry,
	type GateEntry,
	GATE_CONTEXT_MAX_BYTES,
	loadCharter,
	overlayPath,
	parseMode,
	parseVerdict,
	renderTranscript,
	scopedDevice,
} from "../askgate-core";
import * as fx from "./askgate-fixtures";

const HOME = "/home/tester";
const CHARTER_PATH = "/dotfiles/omp/watchdog/askgate.md";
const OVERLAY = `${HOME}/.omp/agent/local-overrides.yml`;
const MODEL = { provider: "anthropic", id: "claude-fable-5-1" };
const USAGE = { input: 1200, output: 40, cacheRead: 300, cacheWrite: 900, cost: 0.0231 };
const ALLOW = '{"decision":"allow"}';
const REVISE = '{"decision":"revise","reason":"failure 7 (content gate): \\"see the message above\\" points at nothing; send the text itself"}';
const bytes = (s: string) => Buffer.byteLength(s, "utf8");

type Completion = { text: string; error?: string; usage?: GateEntry["usage"] };
type Result = { block?: boolean; reason?: string; input?: Record<string, unknown>; additionalContext?: string } | undefined;
type Handler = (event: unknown, ctx: unknown) => Promise<Result>;

/** One session binding of the gate, driven through a fake `pi` and `ctx`. */
function bind(opts: {
	env?: Record<string, string>;
	files?: Record<string, string>;
	complete?: (req: CompleteRequest) => Promise<Completion>;
	kind?: "main" | "sub";
	model?: unknown;
	branch?: () => readonly unknown[];
} = {}) {
	const handlers = new Map<string, Handler>();
	const entries: GateEntry[] = [];
	const debug: string[] = [];
	const notices: string[] = [];
	const dumps: Array<[string, string]> = [];
	const calls: CompleteRequest[] = [];
	const files = new Map(Object.entries({ [CHARTER_PATH]: "# AskGate charter\nJudge the call.", ...opts.files }));
	const pi = {
		on: (event: string, handler: Handler) => handlers.set(event, handler),
		appendEntry: (customType: string, data: GateEntry) => {
			expect(customType).toBe(ADVISOR_GATE_ENTRY_TYPE);
			entries.push(data);
		},
		logger: { debug: (message: string) => debug.push(message), warn: () => {} },
	};
	const complete = opts.complete ?? (async () => ({ text: ALLOW, usage: USAGE }));
	createAskGate({
		env: opts.env ?? {},
		home: HOME,
		now: Date.now,
		readFile: p => files.get(p),
		appendFile: (p, text) => dumps.push([p, text]),
		complete: req => {
			calls.push(req);
			return complete(req);
		},
		charterPath: CHARTER_PATH,
	})(pi as never);
	const ctx = {
		agent: { kind: opts.kind ?? "main", id: "Main", name: "main", depth: 0 },
		hasUI: true,
		ui: { notify: (message: string) => notices.push(message) },
		getSystemPrompt: () => ["PRIMARY SYSTEM RULES", "More rules."],
		models: { resolve: (spec: string) => (spec === "@askgate" ? ("model" in opts ? opts.model : MODEL) : undefined) },
		sessionManager: { getBranch: opts.branch ?? (() => [fx.user("Post the comment.")]), getSessionId: () => "sess-1", getSessionFile: () => "/sessions/s.jsonl" },
	};
	const handler = handlers.get("tool_call");
	const emit = (event: Record<string, unknown>) => {
		if (!handler) throw new Error("no tool_call handler registered");
		return handler({ type: "tool_call", ...event }, ctx);
	};
	return {
		handlers,
		entries,
		debug,
		notices,
		dumps,
		calls,
		files,
		write: (toolCallId: string, path: string, content: string) => emit({ toolName: "write", toolCallId, input: { path, content } }),
		device: (toolCallId: string, toolName: string, input: Record<string, unknown>) => emit({ toolName, toolCallId, input }),
	};
}

const COMMENT = { issue: "X-1", body: "The acceptance run started." };

describe("parseMode (test 1)", () => {
	test("undefined is warn and block is block", () => {
		expect(parseMode(undefined)).toBe("warn");
		expect(parseMode("block")).toBe("block");
	});
	test("off registers no handler", () => {
		expect(bind({ env: { OMP_ASKGATE: "off" } }).handlers.size).toBe(0);
	});
	test("an unknown mode throws when the extension binds", () => {
		expect(() => bind({ env: { OMP_ASKGATE: "bogus" } })).toThrow(/OMP_ASKGATE/);
	});
});

describe("scopedDevice (test 2)", () => {
	test("the six devices are scoped, dispatch_issue only with spec, dispatch_search never", () => {
		for (const name of ["dispatch_ask", "dispatch_message", "dispatch_edit_ask", "dispatch_comment", "dispatch_doc_edit"]) {
			expect(scopedDevice(name, {})).toBe(true);
		}
		expect(scopedDevice("dispatch_issue", { title: "t" })).toBe(false);
		expect(scopedDevice("dispatch_issue", { title: "t", spec: "s" })).toBe(true);
		expect(scopedDevice("dispatch_search", { query: "q" })).toBe(false);
	});
});

describe("rebuttal (test 3)", () => {
	const withRebuttal = '{"issue":"X-1","body":"b","advisor_rebuttal":"a link"}';
	test("the write event loses the key and its device event is recorded as a rebuttal without a model call", async () => {
		const g = bind();
		expect(await g.write("t1", "xd://dispatch_comment", withRebuttal)).toEqual({ input: { path: "xd://dispatch_comment", content: '{"issue":"X-1","body":"b"}' } });
		expect(await g.device("t1", "dispatch_comment", { issue: "X-1", body: "b" })).toBeUndefined();
		expect(g.calls).toHaveLength(0);
		expect(g.entries).toMatchObject([{ decision: "allow", outcome: "rebuttal", rebuttal: "a link", tool: "dispatch_comment", path: "xd://dispatch_comment", toolCallId: "t1" }]);
	});
	test("a rebuttal is spent by its device event: the same id is gated afterwards", async () => {
		const g = bind();
		await g.write("t1", "xd://dispatch_comment", withRebuttal);
		await g.device("t1", "dispatch_comment", { issue: "X-1", body: "b" });
		await g.device("t1", "dispatch_comment", { issue: "X-1", body: "b" });
		expect(g.calls).toHaveLength(1);
		expect(g.entries.map(e => e.outcome)).toEqual(["rebuttal", "verdict"]);
	});
	test("a killed call also spends its rebuttal", async () => {
		const g = bind({ files: { [OVERLAY]: "advisor:\n  disableRoster: [askgate]\n" } });
		await g.write("t1", "xd://dispatch_comment", withRebuttal);
		await g.device("t1", "dispatch_comment", { issue: "X-1", body: "b" });
		g.files.delete(OVERLAY);
		await g.device("t1", "dispatch_comment", { issue: "X-1", body: "b" });
		expect(g.entries.map(e => e.outcome)).toEqual(["killed", "verdict"]);
	});
	test("rebuttals whose device event never fires are capped at 64, oldest evicted", async () => {
		const g = bind();
		for (let i = 0; i < 65; i++) await g.write(`w${i}`, "xd://dispatch_comment", withRebuttal);
		await g.device("w0", "dispatch_comment", { issue: "X-1", body: "b" });
		await g.device("w1", "dispatch_comment", { issue: "X-1", body: "b" });
		expect(g.entries.map(e => e.outcome)).toEqual(["verdict", "rebuttal"]);
	});
	test("a write to an unscoped device and one without the key are untouched", async () => {
		const g = bind();
		expect(await g.write("t2", "xd://dispatch_search", '{"query":"q","advisor_rebuttal":"x"}')).toBeUndefined();
		expect(await g.write("t3", "xd://dispatch_comment", '{"issue":"X-1","body":"b"}')).toBeUndefined();
		expect(g.entries).toHaveLength(0);
	});
});

describe("verdicts", () => {
	test("allow (test 4): recorded with latency, args digest and usage; the call runs", async () => {
		const g = bind({ env: { OMP_ASKGATE_DUMP: "/tmp/dump.txt" } });
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		const [e] = g.entries;
		expect(e).toMatchObject({ advisor: "AskGate", decision: "allow", outcome: "verdict", verdictMode: "warn", usage: USAGE, model: "anthropic/claude-fable-5-1" });
		expect(e.latencyMs).toBeGreaterThanOrEqual(0);
		expect(e.argsDigest).toBe(createHash("sha256").update(JSON.stringify(COMMENT)).digest("hex"));
		expect(e.promptBytes).toBe(bytes(g.calls[0].system) + bytes(g.calls[0].user));
		expect(g.calls[0].model).toEqual(MODEL);
		expect(g.calls[0].system).toContain("<primary-system-prompt>\nPRIMARY SYSTEM RULES\n\nMore rules.\n</primary-system-prompt>");
		expect(g.calls[0].system).toContain("# AskGate charter");
		expect(g.calls[0].user).toContain("### Gate request");
		expect(g.calls[0].user).toContain('"body": "The acceptance run started."');
		expect(g.dumps).toHaveLength(1);
		expect(g.dumps[0][0]).toBe("/tmp/dump.txt");
		expect(g.dumps[0][1]).toContain(g.calls[0].system);
		expect(g.dumps[0][1]).toContain(g.calls[0].user);
	});
	test("no dump without OMP_ASKGATE_DUMP", async () => {
		const g = bind();
		await g.device("t1", "dispatch_comment", COMMENT);
		expect(g.dumps).toHaveLength(0);
	});
	test("revise in warn (test 5) returns the reason as additional context", async () => {
		const g = bind({ complete: async () => ({ text: `Looking at it.\n${REVISE}` }) });
		const result = await g.device("t1", "dispatch_comment", COMMENT);
		expect(result?.block).toBeUndefined();
		expect(result?.additionalContext).toContain('verdict="revise"');
		expect(result?.additionalContext).toContain("see the message above");
		expect(g.entries).toMatchObject([{ decision: "revise", outcome: "verdict", verdictMode: "warn", revisesForKey: 1 }]);
		expect(g.entries[0].reason).toContain("failure 7");
	});
	test("revise in block (test 6) refuses the call with the reason and the rebuttal key", async () => {
		const g = bind({ env: { OMP_ASKGATE: "block" }, complete: async () => ({ text: REVISE }) });
		const result = await g.device("t1", "dispatch_comment", COMMENT);
		expect(result?.block).toBe(true);
		expect(result?.reason).toContain("see the message above");
		expect(result?.reason).toContain("advisor_rebuttal");
		expect(g.entries).toMatchObject([{ decision: "revise", verdictMode: "block" }]);
	});
});

describe("breaker (test 7)", () => {
	test("two revises on one target trip it for that target only, and the trip resets it", async () => {
		const g = bind({ complete: async () => ({ text: REVISE }) });
		await g.device("a", "dispatch_comment", { issue: "X-1", body: "one" });
		await g.device("b", "dispatch_comment", { issue: "X-1", body: "two" });
		await g.device("c", "dispatch_comment", { issue: "X-2", body: "other" });
		expect(g.calls).toHaveLength(3);
		expect(await g.device("d", "dispatch_comment", { issue: "X-1", body: "three" })).toBeUndefined();
		expect(g.calls).toHaveLength(3);
		await g.device("e", "dispatch_comment", { issue: "X-1", body: "four" });
		expect(g.calls).toHaveLength(4);
		expect(g.entries.map(e => `${e.decision}/${e.outcome}`)).toEqual(["revise/verdict", "revise/verdict", "revise/verdict", "allow/breaker", "revise/verdict"]);
		expect(g.entries[3].revisesForKey).toBe(2);
	});
	test("the request carries the reasons already given for the target", async () => {
		const g = bind({ complete: async () => ({ text: REVISE }) });
		await g.device("a", "dispatch_comment", { issue: "X-1", body: "one" });
		await g.device("b", "dispatch_comment", { issue: "X-1", body: "two" });
		expect(g.calls[0].user).not.toContain("You answered revise");
		expect(g.calls[1].user).toContain("You answered revise 1 time(s) for this target since its last allowed call:\n- failure 7");
	});
	test("an allow resets the target", async () => {
		let text = REVISE;
		const g = bind({ complete: async () => ({ text }) });
		await g.device("a", "dispatch_comment", { issue: "X-1", body: "one" });
		text = ALLOW;
		await g.device("b", "dispatch_comment", { issue: "X-1", body: "two" });
		text = REVISE;
		await g.device("c", "dispatch_comment", { issue: "X-1", body: "three" });
		await g.device("d", "dispatch_comment", { issue: "X-1", body: "four" });
		expect(g.calls).toHaveLength(4);
	});
	test("breakerKey names the device and every target field present", () => {
		expect(breakerKey("dispatch_ask", { issue: "X-1", question: "q" })).toBe("dispatch_ask\u0000issue=X-1");
		expect(breakerKey("dispatch_doc_edit", { project: "P", artifact: "a.md" })).toBe("dispatch_doc_edit\u0000artifact=a.md\u0000project=P");
	});
});

describe("fail-open", () => {
	test("timeout (test 8): the call runs within the deadline and the model call is aborted", async () => {
		const signals: AbortSignal[] = [];
		const g = bind({ env: { OMP_ASKGATE_TIMEOUT_MS: "50" }, complete: req => { signals.push(req.signal); return Promise.withResolvers<Completion>().promise; } });
		const started = Date.now();
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		expect(Date.now() - started).toBeLessThan(1000);
		expect(signals[0].aborted).toBe(true);
		expect(g.entries).toMatchObject([{ decision: "allow", outcome: "timeout" }]);
	});
	test("three consecutive failures halt the gate for the session with one notice", async () => {
		const g = bind({ env: { OMP_ASKGATE_TIMEOUT_MS: "20" }, complete: () => Promise.withResolvers<Completion>().promise });
		for (const id of ["a", "b", "c", "d", "e"]) await g.device(id, "dispatch_comment", COMMENT);
		expect(g.entries.map(e => e.outcome)).toEqual(["timeout", "timeout", "timeout", "halted", "halted"]);
		expect(g.calls).toHaveLength(3);
		expect(g.notices).toHaveLength(1);
	});
	test("a verdict between failures resets the count", async () => {
		const texts = ["no json", "no json", ALLOW, "no json", "no json", ALLOW];
		const g = bind({ complete: async () => ({ text: texts.shift() ?? ALLOW }) });
		for (const id of ["a", "b", "c", "d", "e", "f"]) await g.device(id, "dispatch_comment", COMMENT);
		expect(g.entries.map(e => e.outcome)).toEqual(["no-verdict", "no-verdict", "verdict", "no-verdict", "no-verdict", "verdict"]);
	});
	test("errors and unparsable answers (test 9) run the call", async () => {
		const outcomes: string[] = [];
		for (const complete of [
			async (): Promise<Completion> => { throw new Error("provider down"); },
			async (): Promise<Completion> => ({ text: "", error: "x" }),
			async (): Promise<Completion> => ({ text: "I think it is fine." }),
			async (): Promise<Completion> => ({ text: '{"decision":"revise"}' }),
		]) {
			const g = bind({ env: { OMP_ASKGATE: "block" }, complete });
			expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
			outcomes.push(g.entries[0].outcome);
		}
		expect(outcomes).toEqual(["error", "error", "no-verdict", "no-verdict"]);
	});
	test("no model for @askgate runs the call", async () => {
		const g = bind({ model: undefined });
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		expect(g.calls).toHaveLength(0);
		expect(g.entries).toMatchObject([{ decision: "allow", outcome: "unavailable" }]);
	});
	test("a throwing branch read (test 15) is recorded as an error and never throws", async () => {
		const g = bind({ env: { OMP_ASKGATE: "block" }, branch: () => { throw new Error("branch unavailable"); } });
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		expect(g.entries).toMatchObject([{ decision: "allow", outcome: "error" }]);
		expect(g.entries[0].reason).toContain("branch unavailable");
	});
});

describe("kill (test 10)", () => {
	test("askgate under advisor.disableRoster skips the model", async () => {
		const g = bind({ files: { [OVERLAY]: "advisor:\n  disableRoster: [askgate]\n" } });
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		expect(g.calls).toHaveLength(0);
		expect(g.entries).toMatchObject([{ decision: "allow", outcome: "killed" }]);
	});
	test("the overlay is read on every call, so a kill reaches a running session", async () => {
		const g = bind();
		await g.device("a", "dispatch_comment", COMMENT);
		g.files.set(OVERLAY, "advisor:\n  disableRoster: [askgate]\n");
		await g.device("b", "dispatch_comment", COMMENT);
		g.files.delete(OVERLAY);
		await g.device("c", "dispatch_comment", COMMENT);
		expect(g.entries.map(e => e.outcome)).toEqual(["verdict", "killed", "verdict"]);
	});
	test("another member is gated", async () => {
		const g = bind({ files: { [OVERLAY]: "advisor:\n  disableRoster: [memory]\n" } });
		await g.device("t1", "dispatch_comment", COMMENT);
		expect(g.entries.map(e => e.outcome)).toEqual(["verdict"]);
	});
	test("an unparsable overlay is gated, with one debug line", async () => {
		const g = bind({ files: { [OVERLAY]: "advisor: [unclosed\n" } });
		await g.device("t1", "dispatch_comment", COMMENT);
		expect(g.entries.map(e => e.outcome)).toEqual(["verdict"]);
		expect(g.debug).toHaveLength(1);
	});
	test("overlayPath honours PI_CODING_AGENT_DIR", () => {
		expect(overlayPath({}, HOME)).toBe(OVERLAY);
		expect(overlayPath({ PI_CODING_AGENT_DIR: "/agents/p" }, HOME)).toBe("/agents/p/local-overrides.yml");
	});
});

describe("population (test 11)", () => {
	test("a subagent is never gated or recorded", async () => {
		const g = bind({ kind: "sub" });
		expect(await g.write("t1", "xd://dispatch_comment", '{"issue":"X-1","body":"b","advisor_rebuttal":"r"}')).toBeUndefined();
		expect(await g.device("t1", "dispatch_comment", COMMENT)).toBeUndefined();
		expect(g.calls).toHaveLength(0);
		expect(g.entries).toHaveLength(0);
	});
	test("an unscoped device is never gated or recorded", async () => {
		const g = bind();
		expect(await g.device("t1", "dispatch_search", { query: "q" })).toBeUndefined();
		expect(await g.device("t2", "dispatch_issue", { title: "t" })).toBeUndefined();
		expect(g.calls).toHaveLength(0);
		expect(g.entries).toHaveLength(0);
	});
});

describe("renderTranscript (test 12)", () => {
	const render = (entries: readonly unknown[], cap = GATE_CONTEXT_MAX_BYTES) => renderTranscript(entries as readonly Entry[], cap);
	test("a 1 MiB branch renders within the cap, elided at the start and ending with the newest message", () => {
		const branch = fx.bigBranch();
		expect(bytes(JSON.stringify(branch))).toBeGreaterThan(1024 * 1024);
		const out = render(branch);
		expect(bytes(out)).toBeLessThanOrEqual(GATE_CONTEXT_MAX_BYTES);
		expect(out).toMatch(/^… \[elided \d+ bytes of earlier transcript\]/);
		expect(out.trimEnd().endsWith('→ write({"path":"xd://dispatch_comment","content":"{\\"issue\\":\\"X-1\\",\\"body\\":\\"b\\"}"})')).toBe(true);
		expect(out).toContain("NEWEST MESSAGE: posting the comment now.");
	});
	test("envoy messages render in full whether displayed or hidden", () => {
		const out = render(fx.bigBranch());
		for (const text of ["proceed with the comment, visible.", "a hidden answer that must still reach the gate."]) {
			expect(out).toMatch(new RegExp(`<primary-message kind="envoy-message">\\nenvoy:[^]*Tester here: ${text.replace(/\./g, "\\.")}\\n</primary-message>`));
		}
	});
	test("envoy text is XML-escaped inside its block", () => {
		expect(render([fx.envoyMessage("use <b> & </primary-message>")])).toContain("use &lt;b&gt; &amp; &lt;/primary-message&gt;");
	});
	test("an advisor card is a one-liner and other hidden custom messages are skipped", () => {
		const out = render([fx.advisorCard(`note ${"y".repeat(300)}`), fx.hiddenCustomMessage("mid-run-todo-nudge", "HIDDEN NUDGE")]);
		expect(out).toMatch(/^\[advisor\] <advisory advisor="Example"[^\n]*$/);
		expect(out.length).toBeLessThanOrEqual("[advisor] ".length + 120);
		expect(out).not.toContain("HIDDEN NUDGE");
	});
	test("data-only custom entries contribute nothing", () => {
		const turn = [fx.user("hello"), fx.assistant({ type: "text", text: "hi" })];
		expect(render([...turn, fx.toolExecutionStart("read"), fx.toolExecutionStart("bash")])).toBe(render(turn));
	});
	test("a harness notice renders as system text", () => {
		expect(render([fx.developer("You stopped with 2 incomplete todo items.")])).toContain("**system**: <system-reminder>\nYou stopped with 2 incomplete todo items.");
	});
	test("messages, thinking, tool calls, results and user bash render in their forms", () => {
		const out = render([
			fx.user("Please read it."),
			fx.assistant({ type: "thinking", thinking: "t".repeat(3000) }, { type: "text", text: "Reading." }, { type: "toolCall", name: "read", arguments: { path: "/a" } }),
			fx.toolResult("read", "file body"),
			fx.bashExecution("ls /work", "a\nb"),
		]);
		expect(out).toContain("**user**: Please read it.");
		expect(out).toContain("**agent**: (thinking) ttt");
		expect(out).toContain("Reading.\n→ read({\"path\":\"/a\"})");
		expect(out).toContain("⇒ read: file body");
		expect(out).toContain("→ user-bash ls /work ⇒ a\nb");
		const thinkingLine = out.split("\n").find(l => l.startsWith("**agent**: (thinking)")) ?? "";
		expect(bytes(thinkingLine)).toBeLessThanOrEqual(1024 + 40);
	});
	test("a compaction renders its summary and nothing before its first kept entry", () => {
		const old = fx.user("OLD TURN summarized away");
		const kept = fx.user("KEPT TURN");
		const after = fx.assistant({ type: "text", text: "AFTER" });
		const out = render([old, kept, after, fx.compaction("SUMMARY OF EARLIER WORK", kept.id as string), fx.user("NEWER")]);
		expect(out).not.toContain("OLD TURN");
		expect(out).toMatch(/^\[compaction\] SUMMARY OF EARLIER WORK\n\n\*\*user\*\*: KEPT TURN\n\n\*\*agent\*\*: AFTER\n\n\*\*user\*\*: NEWER$/);
	});
	test("nothing before the last reset boundary is rendered", () => {
		const out = render([fx.user("BEFORE CLEAR"), fx.resetBoundary(), fx.user("AFTER CLEAR")]);
		expect(out).not.toContain("BEFORE CLEAR");
		expect(out).toBe("**user**: AFTER CLEAR");
	});
	test("a 20 KiB tool result is cut to 8 KiB", () => {
		const out = render([fx.toolResult("read", "z".repeat(20 * 1024))]);
		expect(out.startsWith("⇒ read: zzz")).toBe(true);
		expect(bytes(out)).toBeLessThanOrEqual(8 * 1024 + "⇒ read: ".length);
		expect(out).toMatch(/… \[elided \d+ bytes\]$/);
	});
	test("an unknown entry type and header lines are skipped without throwing", () => {
		expect(render([fx.titleSlot(), fx.sessionHeader(), fx.modelChange(), fx.unknownEntry(), fx.user("only this")])).toBe("**user**: only this");
	});
});

describe("loadCharter (test 13)", () => {
	test("an @~ import line is inlined", () => {
		const files: Record<string, string> = { "/c/askgate.md": "Charter head.\n@~/x.md\nCharter tail.", [`${HOME}/x.md`]: "IMPORTED BODY" };
		expect(loadCharter(p => files[p], "/c/askgate.md", HOME)).toBe("Charter head.\nIMPORTED BODY\nCharter tail.");
	});
	test("a missing import leaves its line in place", () => {
		const files: Record<string, string> = { "/c/askgate.md": "Head.\n@~/missing.md" };
		expect(loadCharter(p => files[p], "/c/askgate.md", HOME)).toBe("Head.\n@~/missing.md");
	});
	test("a missing charter throws", () => {
		expect(() => loadCharter(() => undefined, "/c/askgate.md", HOME)).toThrow(/askgate\.md/);
	});
});

describe("parseVerdict (test 14)", () => {
	test("JSON on the last line after reasoning is parsed", () => {
		expect(parseVerdict(`The ask is concrete.\n${ALLOW}`)).toEqual({ decision: "allow" });
		expect(parseVerdict(REVISE)?.decision).toBe("revise");
	});
	test("trailing prose after the JSON is no verdict", () => {
		expect(parseVerdict(`${ALLOW}\nHope that helps.`)).toBeUndefined();
	});
	test("a revise without a reason and an unknown decision are no verdict", () => {
		expect(parseVerdict('{"decision":"revise","reason":"  "}')).toBeUndefined();
		expect(parseVerdict('{"decision":"maybe"}')).toBeUndefined();
	});
});

describe("askgate.ts, the entry", () => {
	// The entry imports @oh-my-pi/pi-ai, which resolves only inside omp; this stub stands in for it and records the
	// options of every completion the gate makes.
	const completions: Array<Record<string, unknown>> = [];
	mock.module("@oh-my-pi/pi-ai", () => ({
		completeSimple: async (_model: unknown, _context: unknown, options: Record<string, unknown>) => {
			completions.push(options);
			return {
				content: [{ type: "thinking", thinking: "Checking the call." }, { type: "text", text: ALLOW }],
				usage: { input: 5, output: 7, cacheRead: 0, cacheWrite: 0, totalTokens: 12, cost: { input: 0.05, output: 0.2, cacheRead: 0, cacheWrite: 0, total: 0.25 } },
				stopReason: "stop",
			};
		},
		retryTransientCompletion: (run: () => Promise<unknown>) => run(),
	}));
	const saved = { ...process.env };
	const dirs: string[] = [];
	afterEach(() => {
		completions.length = 0;
		for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
		Object.assign(process.env, saved);
		for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
	});

	/** Binds the real entry to a fake `pi` and runs one gated dispatch_comment through it. */
	async function gateOnce(env: Record<string, string> = {}) {
		const agentDir = fs.mkdtempSync(path.join(tmpdir(), "askgate-entry-"));
		dirs.push(agentDir);
		Object.assign(process.env, { PI_CODING_AGENT_DIR: agentDir }, env);
		delete process.env.OMP_ASKGATE;
		// Imported after mock.module so the entry binds the stub; a static import would load it first.
		const { default: askgate } = await import("../askgate");
		const handlers = new Map<string, Handler>();
		const entries: GateEntry[] = [];
		askgate({ on: (event: string, handler: Handler) => handlers.set(event, handler), appendEntry: (_: string, data: GateEntry) => entries.push(data), logger: { debug: () => {} } } as never);
		const ctx = {
			agent: { kind: "main" },
			hasUI: false,
			ui: { notify: () => {} },
			getSystemPrompt: () => ["PRIMARY"],
			models: { resolve: () => MODEL },
			modelRegistry: { resolver: () => "resolved-key" },
			sessionManager: { getBranch: () => [fx.user("Post it.")], getSessionId: () => "sess-e" },
		};
		const result = await handlers.get("tool_call")?.({ type: "tool_call", toolName: "dispatch_comment", toolCallId: "t1", input: COMMENT }, ctx);
		return { result, entries, agentDir };
	}

	test("the completion runs at high effort with prompt caching off, and its usage is recorded", async () => {
		const { result, entries } = await gateOnce();
		expect(result).toBeUndefined();
		expect(completions).toHaveLength(1);
		expect(completions[0]).toMatchObject({ reasoning: "high", cacheRetention: "none", maxTokens: 1200 });
		expect(completions[0].signal).toBeInstanceOf(AbortSignal);
		expect(entries).toMatchObject([{ decision: "allow", outcome: "verdict", usage: { input: 5, output: 7, cacheRead: 0, cacheWrite: 0, cost: 0.25 } }]);
	});
	test("the prompt dump is created readable by its owner only, whatever the umask", async () => {
		const dir = fs.mkdtempSync(path.join(tmpdir(), "askgate-dump-"));
		dirs.push(dir);
		const dump = path.join(dir, "dump.txt");
		const umask = process.umask(0o022);
		try {
			await gateOnce({ OMP_ASKGATE_DUMP: dump });
		} finally {
			process.umask(umask);
		}
		expect(fs.readFileSync(dump, "utf8")).toContain("### Gate request");
		expect(fs.statSync(dump).mode & 0o777).toBe(0o600);
	});
});
