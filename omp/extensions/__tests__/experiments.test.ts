import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import gateFile from "../experiments/gates.json" with { type: "json" };
import { draw, ENTRY_TYPE, EXCLUDED_ENTRY_TYPE, EXPOSURE_ENTRY_TYPE, type ExcludedRecord, type ExperimentRecord, type ExposureRecord, envName, FEATURES, type Feature, resolve, resolveGates, rootSessionId } from "../experiments/gates";
import selfcheckText from "../experiments/infra805-selfcheck.md" with { type: "text" };
import skillText from "../experiments/infra805-skill.md" with { type: "text" };
import { LEGION_ENV } from "../experiments/proactive-memory-core";
import { DEFAULT_RULES, ompModules } from "./omp-interceptor";

// The entry imports the memory agent's and the judge log's bindings, which import @oh-my-pi/pi-ai; that resolves only inside omp.
mock.module("@oh-my-pi/pi-ai", () => ({ completeSimple: async () => undefined, retryTransientCompletion: (run: () => unknown) => run() }));
// The bash gate's binding imports omp's interceptor modules, which resolve only inside omp: these are the same modules by path, and the rules omp would read.
mock.module("@oh-my-pi/pi-coding-agent/exec/settings", () => ({ cfgBashInterceptorEnabled: { override() {} }, cfgBashInterceptorPatterns: { get: () => DEFAULT_RULES } }));
mock.module("@oh-my-pi/pi-coding-agent/tools/bash-interceptor", () => ompModules.interceptor);
mock.module("@oh-my-pi/pi-coding-agent/tools/shell-tokenize", () => ompModules.tokenize);

const ALL_RANDOM: Record<string, string> = Object.fromEntries(FEATURES.map(feature => [feature, "random"]));
const ids = Array.from({ length: 400 }, (_, i) => `01a0f000-0000-7000-8000-${String(i).padStart(12, "0")}`);
const ROOT = "01a0f8aa-2f87-720f-8260-1d84b54c192b";
const ROOT_FILE = `/home/u/.omp/agent/sessions/-tmp-x/2026-10-01T18-11-37-991Z_${ROOT}.jsonl`;
/** The session file of a root session whose id is `id`. */
const rootFile = (id: string) => `/home/u/.omp/agent/sessions/-tmp-x/2026-10-01T18-11-37-991Z_${id}.jsonl`;

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
		// 2^10 combinations need more ids than the bounds above: 400 draws leave many of them unseen.
		const many = Array.from({ length: 10_000 }, (_, i) => `01a0f000-0000-7000-8000-${String(i).padStart(12, "0")}`);
		const combinations = new Set(many.map(id => FEATURES.map(f => (draw(id, f) ? 1 : 0)).join("")));
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
		expect(gates).toEqual({ ...ALL_RANDOM, selfcompact: "off", skill_gate: "on" });
	});
	test.each([
		["a variable whose value is not a gate", ALL_RANDOM, { OMP_EXPERIMENT_SELFCOMPACT: "maybe" }, /OMP_EXPERIMENT_SELFCOMPACT must be on, off or random, got "maybe"/],
		["an empty variable", ALL_RANDOM, { OMP_EXPERIMENT_SKILL_GATE: "" }, /OMP_EXPERIMENT_SKILL_GATE must be on, off or random, got ""/],
		["a variable naming no feature", ALL_RANDOM, { OMP_EXPERIMENT_SELFCOMPAKT: "on" }, /OMP_EXPERIMENT_SELFCOMPAKT names no feature/],
		["a gate file naming an unknown feature", { ...ALL_RANDOM, budget: "on" }, {}, /unknown feature "budget"/],
		["a gate file missing a feature", { selfcompact: "on", proactive_memory: "on", skill_gate: "on", judge_log: "on" }, {}, /no gate for feature "rules_refresh"/],
		["a gate file giving a feature a non-gate", { ...ALL_RANDOM, proactive_memory: true }, {}, /feature "proactive_memory" the gate true/],
		["a gate file that is not an object", ["selfcompact"], {}, /must map each feature/],
	] as const)("refuses %s, naming it", (_label, file, env, message) => {
		expect(() => resolveGates(file, env as Record<string, string>)).toThrow(message);
	});
});

describe("the gate file", () => {
	test("parses, and holds a gate for every feature in FEATURES and for nothing else", () => {
		expect(() => resolveGates(gateFile, {})).not.toThrow();
		expect(Object.keys(gateFile).sort()).toEqual([...FEATURES].sort());
	});
	test("sets the three features of the eval method's experiments off", () => {
		const record = resolve(resolveGates(gateFile, {}), ROOT);
		for (const feature of ["aa_noop", "infra805_skill", "infra805_selfcheck"] as const) {
			expect(gateFile[feature]).toBe("off");
			expect(record.features[feature].on).toBe(false);
		}
	});
	test.each([
		["the skill text is SKILL.md of cybermonklabs/legion at d806232f, unchanged", skillText, "cab9f7bf7937ded98538de09226e0aab7289e811"],
		["the self-check text is the paragraph written for its arm and the three questions of d806232f", selfcheckText, "17e71c4d29ae7c56b9755d56df61e7a516233bcb"],
	])("%s (git blob %s)", (_label, text, expected) => {
		const bytes = new TextEncoder().encode(text);
		const blob = new Bun.CryptoHasher("sha1").update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
		expect(blob).toBe(expected);
	});
});

describe("the extension", () => {
	type Handler = (event: unknown, ctx: unknown) => unknown;
	const saved = { ...process.env };
	beforeEach(() => {
		for (const name of Object.keys(process.env)) if (name.startsWith("OMP_EXPERIMENT_")) delete process.env[name];
		// The text arms and the memory agent skip a Legion pane: a test runs as a session outside Legion.
		for (const name of LEGION_ENV) delete process.env[name];
	});
	afterEach(() => {
		for (const name of Object.keys(process.env)) if (name.startsWith("OMP_EXPERIMENT_")) delete process.env[name];
		for (const name of LEGION_ENV) delete process.env[name];
		Object.assign(process.env, saved);
	});

	type Delivered = { message: { customType: string; content: string; display: boolean } };
	/** The real entry bound to a fake `pi`, with every gate set from `env`, for a session whose file is `sessionFile`. Its branch is its entries unless `setBranch` replaces it. */
	async function bind(env: Record<string, string>, sessionFile = ROOT_FILE, seed: Array<{ type: string; customType?: string; data?: unknown; [key: string]: unknown }> = [], kind: "main" | "sub" = "main") {
		Object.assign(process.env, env);
		const { default: experiments } = await import("../experiments/index");
		const handlers = new Map<string, Handler[]>();
		const entries = [...seed];
		const errors: string[] = [];
		let appendFails: (customType: string, data: unknown) => boolean = () => false;
		const pi = {
			on: (event: string, handler: Handler) => handlers.set(event, [...(handlers.get(event) ?? []), handler]),
			appendEntry: (customType: string, data: unknown) => {
				if (appendFails(customType, data)) throw new Error("the session file cannot be written");
				entries.push({ type: "custom", customType, data });
			},
			logger: { error: (message: string) => errors.push(message), warn: () => {}, debug: () => {} },
			typebox: { Type: {} },
			pi: { settings: {} },
			getActiveTools: () => ["bash", "read", "grep", "glob", "edit", "write"],
		};
		experiments(pi as never);
		let branch: () => unknown[] = () => entries;
		const ctx = {
			agent: { kind },
			sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => ROOT, getEntries: () => entries, getBranch: () => branch() },
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
			context: () => emit("context", { type: "context", messages: [{ role: "user", content: "hi", timestamp: 1 }] }),
			gh: () => emit("tool_call", { toolName: "bash", toolCallId: "c1", input: { command: "gh pr create --fill" } }) as Promise<{ block?: boolean } | undefined>,
			bash: (command: string) => emit("tool_call", { toolName: "bash", toolCallId: "c2", input: { command } }) as Promise<{ block?: boolean; reason?: string } | undefined>,
			setBranch: (fn: typeof branch) => (branch = fn),
			setAppendFailure: (fn: typeof appendFails) => (appendFails = fn),
			exposures: () => entries.filter(e => e.customType === EXPOSURE_ENTRY_TYPE).map(e => e.data as ExposureRecord),
			excluded: () => entries.filter(e => e.customType === EXCLUDED_ENTRY_TYPE).map(e => e.data as ExcludedRecord),
			/** Every before_agent_start result of one prompt, in handler order. Each message goes on the branch as omp stores it, a `custom_message` entry. */
			prompt: async (reason: "prompt" | "revive" = "prompt") => {
				const results: Delivered[] = [];
				for (const handler of handlers.get("before_agent_start") ?? []) {
					const result = await handler({ type: "before_agent_start", reason, prompt: "Design the cache.", systemPrompt: ["You are omp."] }, ctx);
					if (result !== undefined) results.push(result as Delivered);
				}
				for (const { message } of results) entries.push({ type: "custom_message", ...message });
				return results;
			},
			/** The next request, holding the prompt and the custom messages of `delivered`. Each context handler gets the messages the one before it returned, as in omp. Returns the custom types of the custom messages that go out. */
			request: async (delivered: Delivered[]) => {
				let messages: Array<{ role: string; customType?: string }> = [{ role: "user", content: "Design the cache.", timestamp: 1 }, ...delivered.map(d => ({ role: "custom", ...d.message, timestamp: 2 }))];
				for (const handler of handlers.get("context") ?? []) {
					const result = (await handler({ type: "context", messages }, ctx)) as { messages?: typeof messages } | undefined;
					if (result?.messages) messages = result.messages;
				}
				return messages.filter(m => m.role === "custom").map(m => m.customType);
			},
		};
	}
	const allOff = { OMP_EXPERIMENT_SELFCOMPACT: "off", OMP_EXPERIMENT_PROACTIVE_MEMORY: "off", OMP_EXPERIMENT_SKILL_GATE: "off", OMP_EXPERIMENT_JUDGE_LOG: "off" };

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
		const on = await bind({ ...allOff, OMP_EXPERIMENT_SKILL_GATE: "on" });
		await on.start();
		expect((await on.gh())?.block).toBe(true);
	});
	test("the bash gate runs in both arms: on lets a later read step through, off blocks it as omp does", async () => {
		const on = await bind({ OMP_EXPERIMENT_BASH_GATE_LEADING: "on" });
		await on.start();
		expect(await on.bash("ls d && cat f")).toBeUndefined();
		expect((await on.bash("cat f"))?.block).toBe(true);
		const off = await bind({ OMP_EXPERIMENT_BASH_GATE_LEADING: "off" });
		await off.start();
		expect((await off.bash("ls d && cat f"))?.reason).toContain("`cat f`");
		expect(await off.bash("ps | grep x")).toBeUndefined();
	});
	test("a feature whose handler throws is logged and stays off for the session; the others keep running", async () => {
		const b = await bind({ ...allOff, OMP_EXPERIMENT_SKILL_GATE: "on", OMP_EXPERIMENT_BASH_GATE_LEADING: "on" });
		await b.start();
		b.setBranch(() => {
			throw new Error("branch unavailable");
		});
		expect(await b.gh()).toBeUndefined();
		expect(b.errors).toEqual([expect.stringContaining("skill_gate failed in tool_call")]);
		b.setBranch(() => []);
		expect(await b.gh()).toBeUndefined();
		expect((await b.bash("cat f"))?.block).toBe(true);
	});

	describe("the three features of the eval method's experiments", () => {
		const TEXTS: ReadonlyArray<[Feature, string | undefined]> = [
			["aa_noop", undefined],
			["infra805_skill", skillText],
			["infra805_selfcheck", selfcheckText],
		];
		/** What the handler of `feature` puts in at a prompt: one hidden custom message with its text, or nothing. */
		const deliveryOf = (feature: Feature, text: string | undefined): Delivered[] => (text === undefined ? [] : [{ message: { customType: feature, content: text, display: false } }]);

		test.each(TEXTS)("%s in the on arm adds its text and writes one entry with arm on when the text reaches the session, and no other while the entry stays in the context", async (feature, text) => {
			const drawnOn = ids.find(id => draw(id, feature)) as string;
			for (const [gate, file, root] of [["on", ROOT_FILE, ROOT], ["random", rootFile(drawnOn), drawnOn]] as const) {
				const b = await bind({ [envName(feature)]: gate }, file);
				await b.start();
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf(feature, text));
				expect(b.exposures()).toEqual([]);
				expect(await b.request(delivered)).toEqual(text === undefined ? [] : [feature]);
				expect(b.exposures()).toEqual([{ v: 1, feature, arm: "on", gate, rootSessionId: root, at: expect.any(String) }]);
				const { at } = b.exposures()[0];
				expect(new Date(at).toISOString()).toBe(at);
				expect(Math.abs(Date.parse(at) - Date.now())).toBeLessThan(60_000);
				expect(await b.prompt()).toEqual([]);
				await b.request(delivered);
				expect(b.exposures()).toHaveLength(1);
			}
		});
		test.each(TEXTS)("%s in the off arm of a random gate adds no text and writes one entry with arm off at the next request", async feature => {
			const drawnOff = ids.find(id => !draw(id, feature)) as string;
			const b = await bind({ [envName(feature)]: "random" }, rootFile(drawnOff));
			await b.start();
			expect(b.records()[0].features[feature]).toEqual({ gate: "random", draw: "off", on: false });
			expect(await b.prompt()).toEqual([]);
			expect(b.exposures()).toEqual([]);
			expect(await b.request([])).toEqual([]);
			expect(b.exposures()).toEqual([{ v: 1, feature, arm: "off", gate: "random", rootSessionId: drawnOff, at: expect.any(String) }]);
			expect(await b.prompt()).toEqual([]);
			await b.request([]);
			expect(b.exposures()).toHaveLength(1);
		});
		test.each(TEXTS)("%s with its gate off adds nothing and writes no entry", async (feature, text) => {
			const b = await bind({ [envName(feature)]: "off" });
			await b.start();
			expect(await b.prompt()).toEqual([]);
			await b.request(deliveryOf(feature, text));
			expect(b.exposures()).toEqual([]);
		});
		test("the exposure entry of the on arm waits for the request whose messages hold the text", async () => {
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
			await b.start();
			const delivered = await b.prompt();
			await b.request([]);
			expect(b.exposures()).toEqual([]);
			await b.request(delivered);
			expect(b.exposures()).toHaveLength(1);
		});
		test("aa_noop changes no prompt, request or tool call: its two arms differ only in the arm of its exposure entry", async () => {
			const run = async (root: string) => {
				const env = { ...allOff, OMP_EXPERIMENT_SKILL_GATE: "on", OMP_EXPERIMENT_RULES_REFRESH: "on", OMP_EXPERIMENT_BASH_GATE_LEADING: "on", OMP_EXPERIMENT_AA_NOOP: "random" };
				const b = await bind(env, rootFile(root));
				await b.start();
				const seen = [await b.prompt(), await b.request([]), await b.gh(), await b.bash("ls d && cat f"), await b.bash("cat f")];
				expect(b.entries.filter(e => e.customType !== ENTRY_TYPE && e.customType !== EXPOSURE_ENTRY_TYPE)).toEqual([]);
				const [exposure, ...rest] = b.exposures();
				expect(rest).toEqual([]);
				const { rootSessionId: _root, at: _at, ...kept } = exposure;
				return { seen, kept };
			};
			const on = await run(ids.find(id => draw(id, "aa_noop")) as string);
			const off = await run(ids.find(id => !draw(id, "aa_noop")) as string);
			expect(on.seen).toEqual(off.seen);
			expect(on.kept).toEqual({ v: 1, feature: "aa_noop", arm: "on", gate: "random" });
			expect(off.kept).toEqual({ ...on.kept, arm: "off" });
		});
		test("a text arm that throws is logged and stops for the session; the other features keep running", async () => {
			const b = await bind({ OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
			await b.start();
			// The first reader of the branch at a prompt is aa_noop, the first text arm.
			let calls = 0;
			b.setBranch(() => {
				if (calls++ === 0) throw new Error("branch unavailable");
				return b.entries;
			});
			const delivered = await b.prompt();
			expect(b.errors).toEqual([expect.stringContaining("aa_noop failed in before_agent_start")]);
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			await b.request(delivered);
			b.entries.push({ type: "compaction" });
			await b.request(await b.prompt());
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck", "infra805_skill", "infra805_selfcheck"]);
			expect((await b.gh())?.block).toBe(true);
		});
		test("with both INFRA-805 gates on, a prompt gets the skill and then the self-check, and each writes its own exposure entry", async () => {
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" });
			await b.start();
			const delivered = await b.prompt();
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			await b.request(delivered);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck"]);
		});
		test("a subagent gets no text and writes no exposure entry, and a revive puts nothing in", async () => {
			const all = { OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" };
			const sub = await bind(all, ROOT_FILE.replace(/\.jsonl$/, "/Worker.jsonl"), [], "sub");
			await sub.start();
			expect(await sub.prompt()).toEqual([]);
			await sub.request([]);
			expect(sub.exposures()).toEqual([]);
			const main = await bind(all);
			await main.start();
			expect(await main.prompt("revive")).toEqual([]);
		});
		test("the text goes in again after a compaction removes it, and not again when the session resumes", async () => {
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
			await b.start();
			await b.request(await b.prompt());
			const resumed = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, b.entries);
			await resumed.start();
			expect(await resumed.prompt()).toEqual([]);
			resumed.entries.push({ type: "compaction" });
			const again = await resumed.prompt();
			expect(again).toEqual(deliveryOf("infra805_skill", skillText));
			await resumed.request(again);
			expect(resumed.exposures()).toHaveLength(2);
		});
		test("the text goes in again when the context keeps the exposure entry but not the text, and no second entry is written", async () => {
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
			await b.start();
			await b.request(await b.prompt());
			const text = b.entries.find(e => e.type === "custom_message" && e.customType === "infra805_skill");
			const [exposure] = b.entries.filter(e => e.customType === EXPOSURE_ENTRY_TYPE);
			// A compaction whose first kept entry is the exposure entry: the text before it is out of the context.
			b.setBranch(() => [{ ...text, id: "t" }, { ...exposure, id: "x" }, { type: "compaction", id: "c", firstKeptEntryId: "x" }]);
			const again = await b.prompt();
			expect(again).toEqual(deliveryOf("infra805_skill", skillText));
			expect(await b.request(again)).toEqual(["infra805_skill"]);
			expect(b.exposures()).toHaveLength(1);
		});
		test("a context that keeps the text but no exposure entry gets no second text, and gets the entry at the next request that holds the text", async () => {
			const kept = { type: "custom_message", customType: "infra805_skill", content: skillText, display: false };
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, [kept]);
			await b.start();
			expect(await b.prompt()).toEqual([]);
			await b.request([]);
			expect(b.exposures()).toEqual([]);
			expect(await b.request(deliveryOf("infra805_skill", skillText))).toEqual(["infra805_skill"]);
			expect(b.exposures().map(e => [e.feature, e.arm])).toEqual([["infra805_skill", "on"]]);
		});
		test("when the exposure append fails, no request carries that feature's text, the feature stops and is logged, and the other features run on", async () => {
			const b = await bind({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
			await b.start();
			b.setAppendFailure((customType, data) => customType === EXPOSURE_ENTRY_TYPE && (data as ExposureRecord).feature === "infra805_skill");
			const delivered = await b.prompt();
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			expect(await b.request(delivered)).toEqual(["infra805_selfcheck"]);
			expect(b.errors).toEqual([expect.stringContaining("infra805_skill failed in context")]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			// Later requests carry both texts from the session's history. The skill's text stays out, also once the append works again.
			b.setAppendFailure(() => false);
			expect(await b.request(delivered)).toEqual(["infra805_selfcheck"]);
			expect(await b.prompt()).toEqual([]);
			expect(await b.request(delivered)).toEqual(["infra805_selfcheck"]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			expect((await b.gh())?.block).toBe(true);
			// The next resolution finds the skill's text in the context and no entry: the entry goes in with the next request that holds the text.
			await b.start();
			expect(await b.prompt()).toEqual([]);
			expect(await b.request(delivered)).toEqual(["infra805_skill", "infra805_selfcheck"]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck", "infra805_skill"]);
		});
		test.each(LEGION_ENV)("a Legion pane (%s set) gets one excluded entry per feature whose gate is on or random, no exposure entry and no text, and an empty value is not a Legion pane", async name => {
			const all = { OMP_EXPERIMENT_AA_NOOP: "random", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" };
			const legion = await bind({ ...all, [name]: "x" });
			await legion.start();
			expect(legion.records()[0].features.infra805_skill.on).toBe(true);
			expect(await legion.prompt("revive")).toEqual([]);
			expect(legion.excluded()).toEqual([]);
			expect(await legion.prompt()).toEqual([]);
			expect(await legion.request([])).toEqual([]);
			expect(legion.exposures()).toEqual([]);
			expect(legion.excluded()).toEqual(
				(["aa_noop", "infra805_skill", "infra805_selfcheck"] as const).map(feature => ({ v: 1, feature, reason: "legion_pane", rootSessionId: ROOT, at: expect.any(String) })),
			);
			expect(await legion.prompt()).toEqual([]);
			const resumed = await bind({ ...all, [name]: "x" }, ROOT_FILE, legion.entries);
			await resumed.start();
			expect(await resumed.prompt()).toEqual([]);
			expect(resumed.excluded()).toHaveLength(3);
			expect(resumed.exposures()).toEqual([]);
			const skillOff = await bind({ ...all, OMP_EXPERIMENT_INFRA805_SKILL: "off", [name]: "x" });
			await skillOff.start();
			await skillOff.prompt();
			expect(skillOff.excluded().map(e => e.feature)).toEqual(["aa_noop", "infra805_selfcheck"]);
			const outside = await bind({ ...all, [name]: "" });
			await outside.start();
			const delivered = await outside.prompt();
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			await outside.request(delivered);
			expect(outside.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill", "infra805_selfcheck"]);
		});
	});
});
