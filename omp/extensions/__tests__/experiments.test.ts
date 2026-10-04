import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import gateFile from "../experiments/gates.json" with { type: "json" };
import { draw, ENTRY_TYPE, type ExperimentRecord, FEATURES, resolve, resolveGates, rootSessionId } from "../experiments/gates";

// The entry imports the memory agent's and the judge log's bindings, which import @oh-my-pi/pi-ai; that resolves only inside omp.
mock.module("@oh-my-pi/pi-ai", () => ({ completeSimple: async () => undefined, retryTransientCompletion: (run: () => unknown) => run() }));

const ALL_RANDOM = { selfcompact: "random", proactive_memory: "random", context_line: "random", skill_gate: "random", judge_log: "random" };
const ids = Array.from({ length: 400 }, (_, i) => `01a0f000-0000-7000-8000-${String(i).padStart(12, "0")}`);
const ROOT = "01a0f8aa-2f87-720f-8260-1d84b54c192b";
const ROOT_FILE = `/home/u/.omp/agent/sessions/-tmp-x/2026-10-01T18-11-37-991Z_${ROOT}.jsonl`;

describe("the draw", () => {
	test("is stable for a root session: the same id draws the same features every time", () => {
		for (const feature of FEATURES) expect(draw(ROOT, feature)).toBe(draw(ROOT, feature));
		expect(resolve(resolveGates(ALL_RANDOM, {}), ROOT)).toEqual(resolve(resolveGates(ALL_RANDOM, {}), ROOT));
	});
	test("is drawn independently per feature: each is on in about half the sessions, and every combination occurs", () => {
		for (const feature of FEATURES) {
			const on = ids.filter(id => draw(id, feature)).length;
			expect(on).toBeGreaterThan(150);
			expect(on).toBeLessThan(250);
		}
		for (const a of FEATURES) for (const b of FEATURES) if (a < b) expect(ids.filter(id => draw(id, a) !== draw(id, b)).length).toBeGreaterThan(150);
		const combinations = new Set(ids.map(id => FEATURES.map(f => (draw(id, f) ? 1 : 0)).join("")));
		expect(combinations.size).toBe(2 ** FEATURES.length);
	});
	test("a subagent draws from its root: the root id is the first Z_<uuid> in the session file's path", () => {
		const root = ROOT_FILE.replace(/\.jsonl$/, "");
		expect(rootSessionId(ROOT_FILE, ROOT)).toBe(ROOT);
		expect(rootSessionId(`${root}/Worker.jsonl`, "01a0f8ab-0000-7000-8000-000000000001")).toBe(ROOT);
		expect(rootSessionId(`${root}/Worker/Reviewer.jsonl`, "01a0f8ab-0000-7000-8000-000000000002")).toBe(ROOT);
	});
	test("a session with no file draws from its own id", () => {
		expect(rootSessionId(undefined, ROOT)).toBe(ROOT);
		expect(rootSessionId("/tmp/no-id-here.jsonl", ROOT)).toBe(ROOT);
	});
});

describe("resolveGates", () => {
	test("on and off force a feature whatever the draw; random takes the draw", () => {
		const on = ids.find(id => draw(id, "selfcompact")) as string;
		const off = ids.find(id => !draw(id, "selfcompact")) as string;
		const forced = (gate: string, id: string) => resolve(resolveGates({ ...ALL_RANDOM, selfcompact: gate }, {}), id).features.selfcompact;
		expect(forced("on", off)).toEqual({ gate: "on", draw: "off", on: true });
		expect(forced("off", on)).toEqual({ gate: "off", draw: "on", on: false });
		expect(forced("random", on)).toEqual({ gate: "random", draw: "on", on: true });
		expect(forced("random", off)).toEqual({ gate: "random", draw: "off", on: false });
	});
	test("OMP_EXPERIMENT_<FEATURE> overrides the file for that feature only", () => {
		const gates = resolveGates(ALL_RANDOM, { OMP_EXPERIMENT_SELFCOMPACT: "off", OMP_EXPERIMENT_SKILL_GATE: "on", OTHER: "x" });
		expect(gates).toEqual({ selfcompact: "off", proactive_memory: "random", context_line: "random", skill_gate: "on", judge_log: "random" });
	});
	test.each([
		["a variable whose value is not a gate", ALL_RANDOM, { OMP_EXPERIMENT_SELFCOMPACT: "maybe" }, /OMP_EXPERIMENT_SELFCOMPACT must be on, off or random, got "maybe"/],
		["an empty variable", ALL_RANDOM, { OMP_EXPERIMENT_CONTEXT_LINE: "" }, /OMP_EXPERIMENT_CONTEXT_LINE must be on, off or random, got ""/],
		["a variable naming no feature", ALL_RANDOM, { OMP_EXPERIMENT_SELFCOMPAKT: "on" }, /OMP_EXPERIMENT_SELFCOMPAKT names no feature/],
		["a gate file naming an unknown feature", { ...ALL_RANDOM, budget: "on" }, {}, /unknown feature "budget"/],
		["a gate file missing a feature", { selfcompact: "on", proactive_memory: "on", context_line: "on", skill_gate: "on" }, {}, /no gate for feature "judge_log"/],
		["a gate file giving a feature a non-gate", { ...ALL_RANDOM, proactive_memory: true }, {}, /feature "proactive_memory" the gate true/],
		["a gate file that is not an object", ["selfcompact"], {}, /must map each feature/],
	] as const)("refuses %s, naming it", (_label, file, env, message) => {
		expect(() => resolveGates(file, env as Record<string, string>)).toThrow(message);
	});
});

describe("the extension", () => {
	type Handler = (event: unknown, ctx: unknown) => unknown;
	const saved = { ...process.env };
	beforeEach(() => {
		for (const name of Object.keys(process.env)) if (name.startsWith("OMP_EXPERIMENT_")) delete process.env[name];
	});
	afterEach(() => {
		for (const name of Object.keys(process.env)) if (name.startsWith("OMP_EXPERIMENT_")) delete process.env[name];
		Object.assign(process.env, saved);
	});

	/** The real entry bound to a fake `pi`, with every gate set from `env`, for a session whose file is `sessionFile`. */
	async function bind(env: Record<string, string>, sessionFile = ROOT_FILE, seed: Array<{ type: string; customType?: string; data?: unknown }> = []) {
		Object.assign(process.env, env);
		const { default: experiments } = await import("../experiments/index");
		const handlers = new Map<string, Handler[]>();
		const entries = [...seed];
		const errors: string[] = [];
		const pi = {
			on: (event: string, handler: Handler) => handlers.set(event, [...(handlers.get(event) ?? []), handler]),
			appendEntry: (customType: string, data: unknown) => entries.push({ type: "custom", customType, data }),
			logger: { error: (message: string) => errors.push(message), warn: () => {}, debug: () => {} },
			typebox: { Type: {} },
		};
		experiments(pi as never);
		let usage: () => { tokens: number; contextWindow: number } = () => ({ tokens: 1_000, contextWindow: 200_000 });
		const ctx = {
			agent: { kind: "main" },
			sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => ROOT, getEntries: () => entries, getBranch: () => [] },
			getContextUsage: () => usage(),
		};
		const emit = async (event: string, payload: unknown) => {
			let out: unknown;
			for (const handler of handlers.get(event) ?? []) out = (await handler(payload, ctx)) ?? out;
			return out;
		};
		return {
			entries,
			errors,
			records: () => entries.filter(e => e.customType === ENTRY_TYPE).map(e => e.data as ExperimentRecord),
			start: () => emit("session_start", { type: "session_start" }),
			context: () => emit("context", { type: "context", messages: [{ role: "user", content: "hi", timestamp: 1 }] }) as Promise<{ messages: Array<{ content: string }> } | undefined>,
			gh: () => emit("tool_call", { toolName: "bash", toolCallId: "c1", input: { command: "gh pr create --fill" } }) as Promise<{ block?: boolean } | undefined>,
			setUsage: (fn: typeof usage) => (usage = fn),
		};
	}
	const allOff = { OMP_EXPERIMENT_SELFCOMPACT: "off", OMP_EXPERIMENT_PROACTIVE_MEMORY: "off", OMP_EXPERIMENT_CONTEXT_LINE: "off", OMP_EXPERIMENT_SKILL_GATE: "off", OMP_EXPERIMENT_JUDGE_LOG: "off" };

	test("a bad variable stops the load, naming it", async () => {
		await expect(bind({ OMP_EXPERIMENT_SELFCOMPACT: "maybe" })).rejects.toThrow(/OMP_EXPERIMENT_SELFCOMPACT/);
	});
	test("session start records one entry naming every feature's gate and draw; a resume that resolves the same adds none", async () => {
		const b = await bind({ OMP_EXPERIMENT_SELFCOMPACT: "on" });
		await b.start();
		const record = resolve(resolveGates(gateFile, { OMP_EXPERIMENT_SELFCOMPACT: "on" }), ROOT);
		expect(b.records()).toEqual([record]);
		const resumed = await bind({ OMP_EXPERIMENT_SELFCOMPACT: "on" }, ROOT_FILE, b.entries);
		await resumed.start();
		expect(resumed.records()).toEqual([record]);
		const relaunched = await bind({ OMP_EXPERIMENT_SELFCOMPACT: "off" }, ROOT_FILE, b.entries);
		await relaunched.start();
		expect(relaunched.records().map(r => r.features.selfcompact.gate)).toEqual(["on", "off"]);
	});
	test("a subagent records the same draws as its root", async () => {
		const root = await bind({});
		await root.start();
		const sub = await bind({}, ROOT_FILE.replace(/\.jsonl$/, "/Worker.jsonl"));
		await sub.start();
		expect(sub.records()).toEqual(root.records());
	});
	test("a feature runs only while its gate is on", async () => {
		const off = await bind(allOff);
		await off.start();
		expect(await off.context()).toBeUndefined();
		expect(await off.gh()).toBeUndefined();
		const on = await bind({ ...allOff, OMP_EXPERIMENT_CONTEXT_LINE: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
		await on.start();
		expect((await on.context())?.messages.at(-1)?.content).toBe("hi\n\n[Token usage: 1,000/200,000]");
		expect((await on.gh())?.block).toBe(true);
	});
	test("a feature whose handler throws is logged and stays off for the session; the others keep running", async () => {
		const b = await bind({ ...allOff, OMP_EXPERIMENT_CONTEXT_LINE: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
		await b.start();
		b.setUsage(() => {
			throw new Error("usage unavailable");
		});
		expect(await b.context()).toBeUndefined();
		expect(b.errors).toEqual([expect.stringContaining("context_line failed in context")]);
		b.setUsage(() => ({ tokens: 1_000, contextWindow: 200_000 }));
		expect(await b.context()).toBeUndefined();
		expect((await b.gh())?.block).toBe(true);
	});
});
