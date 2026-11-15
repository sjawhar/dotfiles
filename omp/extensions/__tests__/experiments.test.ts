import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import { mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
const ALL_OFF: Record<string, string> = Object.fromEntries(FEATURES.map(feature => [feature, "off"]));
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
	test("an all-off gate file resolves every feature off, whatever the draw", () => {
		for (const id of ids.slice(0, 20)) for (const resolution of Object.values(resolve(resolveGates(ALL_OFF, {}), id).features)) expect(resolution.on).toBe(false);
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
	/** The real entry bound to a fake `pi`, with every gate set from `env`, for a session whose file is `sessionFile`. Its branch is its entries unless `setBranch` replaces it. The text arms follow `gatesPath` when one is given, else the committed gates.json, which no test writes. */
	async function bind(env: Record<string, string>, sessionFile = ROOT_FILE, seed: Array<{ type: string; customType?: string; data?: unknown; [key: string]: unknown }> = [], kind: "main" | "sub" = "main", gatesPath?: string) {
		Object.assign(process.env, env);
		const { default: experiments, bindExperiments } = await import("../experiments/index");
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
		if (gatesPath === undefined) experiments(pi as never);
		else bindExperiments(pi as never, gatesPath);
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
		type Message = { role: string; customType?: string; content?: unknown };
		let sent: Message[] = [];
		/** Runs every context handler over `input`, each getting what the one before it returned, as omp does. */
		const send = async (input: Message[]): Promise<Message[]> => {
			let messages = input;
			for (const handler of handlers.get("context") ?? []) {
				const result = (await handler({ type: "context", messages }, ctx)) as { messages?: Message[] } | undefined;
				if (result?.messages) messages = result.messages;
			}
			sent = messages;
			return messages;
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
			/** The next request: `input`, the message that started the run (a typed user prompt unless given), then the custom messages of `delivered`. Each context handler gets the messages the one before it returned, as in omp. Returns the custom types of the custom messages that go out. */
			request: async (delivered: Delivered[], input: Record<string, unknown> = { role: "user", content: "Design the cache.", attribution: "user" }) =>
				(await send([{ role: "user", ...input, timestamp: 1 }, ...delivered.map(d => ({ role: "custom", ...d.message, timestamp: 2 }))])).filter(m => m.role === "custom").map(m => m.customType),
			/** A pre-prompt compaction's request: the stored branch, which ends in the previous run's assistant message, without the new prompt. Returns the custom types that go out. */
			compactionRequest: async (kept: Delivered[] = []) =>
				(
					await send([
						{ role: "user", content: "An earlier prompt.", attribution: "user", timestamp: 0 },
						...kept.map(d => ({ role: "custom", ...d.message, timestamp: 0 })),
						{ role: "assistant", content: [{ type: "text", text: "Done." }], timestamp: 0 },
					])
				)
					.filter(m => m.role === "custom")
					.map(m => m.customType),
			/** The custom messages that went out in the latest request, with their content. */
			sentCustom: () => sent.filter(m => m.role === "custom"),
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
		/** The three gates of this block start off in every session here, whatever gates.json commits, so a change of gates.json (S8 step 4) changes no test. */
		const TEXT_OFF = { OMP_EXPERIMENT_AA_NOOP: "off", OMP_EXPERIMENT_INFRA805_SKILL: "off", OMP_EXPERIMENT_INFRA805_SELFCHECK: "off" };
		const bindArms: typeof bind = (env, ...rest) => bind({ ...TEXT_OFF, ...env }, ...rest);
		/** What the handler of `feature` puts in at a prompt: one hidden custom message with its text, or nothing. */
		const deliveryOf = (feature: Feature, text: string | undefined): Delivered[] => (text === undefined ? [] : [{ message: { customType: feature, content: text, display: false } }]);
		/** The skill's text as a session file keeps it after an earlier prompt. */
		const KEPT_SKILL = { type: "custom_message", customType: "infra805_skill", content: skillText, display: false };

		test.each(TEXTS)("%s in the on arm adds its text and writes one entry with arm on when the text reaches the session, and no other while the entry stays in the context", async (feature, text) => {
			const drawnOn = ids.find(id => draw(id, feature)) as string;
			for (const [gate, file, root] of [["on", ROOT_FILE, ROOT], ["random", rootFile(drawnOn), drawnOn]] as const) {
				const b = await bindArms({ [envName(feature)]: gate }, file);
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
			const b = await bindArms({ [envName(feature)]: "random" }, rootFile(drawnOff));
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
		test.each(TEXTS)("%s with its gate off adds nothing, writes no entry, and lets no text of its feature through", async (feature, text) => {
			const b = await bindArms({ [envName(feature)]: "off" });
			await b.start();
			expect(await b.prompt()).toEqual([]);
			expect(await b.request(deliveryOf(feature, text))).toEqual([]);
			expect(b.exposures()).toEqual([]);
		});
		test("the exposure entry of the on arm waits for the request whose messages hold the text", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
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
				const b = await bindArms(env, rootFile(root));
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
			const b = await bindArms({ OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
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
		test("a text arm that throws in any handler keeps its kept text out of every request, and the other features run on", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" }, ROOT_FILE, [KEPT_SKILL]);
			await b.start();
			// aa_noop is off here, so the first reader of the branch at a prompt is infra805_skill.
			let calls = 0;
			b.setBranch(() => {
				if (calls++ === 0) throw new Error("branch unavailable");
				return b.entries;
			});
			const delivered = await b.prompt();
			expect(b.errors).toEqual([expect.stringContaining("infra805_skill failed in before_agent_start")]);
			expect(delivered).toEqual(deliveryOf("infra805_selfcheck", selfcheckText));
			const history = [...deliveryOf("infra805_skill", skillText), ...delivered];
			expect(await b.request(history)).toEqual(["infra805_selfcheck"]);
			expect(await b.request(history)).toEqual(["infra805_selfcheck"]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			expect((await b.gh())?.block).toBe(true);
		});
		test("with both INFRA-805 gates on, a prompt gets the skill and then the self-check, and each writes its own exposure entry", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" });
			await b.start();
			const delivered = await b.prompt();
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			await b.request(delivered);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck"]);
		});
		test("a subagent gets no text and writes no exposure entry, and a revive puts nothing in", async () => {
			const all = { OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" };
			const sub = await bindArms(all, ROOT_FILE.replace(/\.jsonl$/, "/Worker.jsonl"), [], "sub");
			await sub.start();
			expect(await sub.prompt()).toEqual([]);
			await sub.request([]);
			expect(sub.exposures()).toEqual([]);
			const main = await bindArms(all);
			await main.start();
			expect(await main.prompt("revive")).toEqual([]);
		});
		test("the text goes in again after a compaction removes it, and not again when the session resumes", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
			await b.start();
			await b.request(await b.prompt());
			const resumed = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, b.entries);
			await resumed.start();
			expect(await resumed.prompt()).toEqual([]);
			resumed.entries.push({ type: "compaction" });
			const again = await resumed.prompt();
			expect(again).toEqual(deliveryOf("infra805_skill", skillText));
			await resumed.request(again);
			expect(resumed.exposures()).toHaveLength(2);
		});
		test("the text goes in again when the context keeps the exposure entry but not the text, and no second entry is written", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
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
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, [KEPT_SKILL]);
			await b.start();
			expect(await b.prompt()).toEqual([]);
			await b.request([]);
			expect(b.exposures()).toEqual([]);
			expect(await b.request(deliveryOf("infra805_skill", skillText))).toEqual(["infra805_skill"]);
			expect(b.exposures().map(e => [e.feature, e.arm])).toEqual([["infra805_skill", "on"]]);
		});
		test("a resume that keeps the text with no exposure entry sends none of it when the arm is no longer on: random with the draw off writes an entry with arm off, off writes none", async () => {
			const drawnOff = ids.find(id => !draw(id, "infra805_skill")) as string;
			const control = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "random" }, rootFile(drawnOff), [KEPT_SKILL]);
			await control.start();
			expect(await control.prompt()).toEqual([]);
			expect(await control.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
			expect(control.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: "off", gate: "random", rootSessionId: drawnOff, at: expect.any(String) }]);
			const disabled = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "off" }, ROOT_FILE, [KEPT_SKILL]);
			await disabled.start();
			expect(await disabled.prompt()).toEqual([]);
			expect(await disabled.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
			expect(disabled.exposures()).toEqual([]);
		});
		test("a treated session whose gate goes off sends no text of that feature from its next request on; a request before the first resolution sends none", async () => {
			const treated = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" });
			expect(await treated.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
			await treated.start();
			const delivered = await treated.prompt();
			expect(await treated.request(delivered)).toEqual(["infra805_skill", "infra805_selfcheck"]);
			const resumed = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "off", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" }, ROOT_FILE, treated.entries);
			await resumed.start();
			expect(await resumed.prompt()).toEqual([]);
			expect(await resumed.request(delivered)).toEqual(["infra805_selfcheck"]);
			expect(resumed.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck"]);
		});
		test("when the exposure append fails, no request carries that feature's text, the feature stops and is logged, and the other features run on", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
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
			const legion = await bindArms({ ...all, [name]: "x" });
			await legion.start();
			expect(legion.records()[0].features.infra805_skill.on).toBe(true);
			expect(await legion.prompt("revive")).toEqual([]);
			expect(legion.excluded()).toEqual([]);
			expect(await legion.prompt()).toEqual([]);
			expect(await legion.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
			expect(legion.exposures()).toEqual([]);
			expect(legion.excluded()).toEqual(
				(["aa_noop", "infra805_skill", "infra805_selfcheck"] as const).map(feature => ({ v: 1, feature, reason: "legion_pane", rootSessionId: ROOT, at: expect.any(String) })),
			);
			expect(await legion.prompt()).toEqual([]);
			const resumed = await bindArms({ ...all, [name]: "x" }, ROOT_FILE, legion.entries);
			await resumed.start();
			expect(await resumed.prompt()).toEqual([]);
			expect(resumed.excluded()).toHaveLength(3);
			expect(resumed.exposures()).toEqual([]);
			const skillOff = await bindArms({ ...all, OMP_EXPERIMENT_INFRA805_SKILL: "off", [name]: "x" });
			await skillOff.start();
			await skillOff.prompt();
			await skillOff.request([]);
			expect(skillOff.excluded().map(e => e.feature)).toEqual(["aa_noop", "infra805_selfcheck"]);
			const synthetic = await bindArms({ ...all, [name]: "x" });
			await synthetic.start();
			await synthetic.prompt();
			await synthetic.request([], { role: "developer", content: "Continue.", attribution: "agent", synthetic: true });
			expect(synthetic.excluded()).toEqual([]);
			const outside = await bindArms({ ...all, [name]: "" });
			await outside.start();
			const delivered = await outside.prompt();
			expect(delivered).toEqual([...deliveryOf("infra805_skill", skillText), ...deliveryOf("infra805_selfcheck", selfcheckText)]);
			await outside.request(delivered);
			expect(outside.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill", "infra805_selfcheck"]);
		});

		describe("gates.json changed on disk while omp runs", () => {
			let dir: string;
			let path: string;
			/** Writes the temp copy: the committed gates with the three text gates off and `changes` on top, or `contents` as it is. Its mtime moves to `second`. */
			const writeGates = (contents: Record<string, string> | string, second: number) => {
				writeFileSync(path, typeof contents === "string" ? contents : JSON.stringify({ ...gateFile, aa_noop: "off", infra805_skill: "off", infra805_selfcheck: "off", ...contents }));
				utimesSync(path, second, second);
			};
			beforeEach(() => {
				dir = mkdtempSync(join(tmpdir(), "experiments-gates-"));
				path = join(dir, "gates.json");
			});
			afterEach(() => rmSync(dir, { recursive: true, force: true }));

			test("an off gate written while a session runs stops the text at the next request, with an `experiments` entry and no new exposure, and the other features keep their load-time gates", async () => {
				writeGates({ infra805_skill: "on", bash_gate_leading: "off" }, 1_000);
				const b = await bind({}, ROOT_FILE, [], "main", path);
				await b.start();
				const [first] = b.records();
				expect(first.features.infra805_skill).toMatchObject({ gate: "on", on: true });
				expect(first.features.bash_gate_leading.gate).toBe(gateFile.bash_gate_leading);
				const delivered = await b.prompt();
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures()).toHaveLength(1);
				writeGates({ infra805_skill: "off", bash_gate_leading: "off" }, 2_000);
				expect(await b.request(delivered)).toEqual([]);
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.infra805_skill).toMatchObject({ gate: "off", on: false });
				expect(records[1].features.bash_gate_leading).toEqual(first.features.bash_gate_leading);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.exposures()).toHaveLength(1);
				expect(b.records()).toHaveLength(2);
			});
			test("a gates.json that cannot be parsed keeps the last good gates and is logged once", async () => {
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, ROOT_FILE, [], "main", path);
				await b.start();
				const delivered = await b.prompt();
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				writeGates("{ not json", 2_000);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.errors).toEqual([expect.stringContaining(`the text arms cannot read the gates in ${path}`)]);
				expect(b.records()).toHaveLength(1);
				writeGates({ infra805_skill: "off" }, 3_000);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.records()).toHaveLength(2);
				expect(b.errors).toHaveLength(1);
			});
			test.each([true, false])("aa_noop going random mid-process (draw on: %p) makes the next prompt write an `experiments` entry and an exposure entry with the drawn arm", async drawnOn => {
				const id = ids.find(i => draw(i, "aa_noop") === drawnOn) as string;
				const arm = drawnOn ? "on" : "off";
				writeGates({}, 1_000);
				const b = await bind({}, rootFile(id), [], "main", path);
				await b.start();
				await b.request(await b.prompt());
				expect(b.exposures()).toEqual([]);
				writeGates({ aa_noop: "random" }, 2_000);
				expect(await b.prompt()).toEqual([]);
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.aa_noop).toEqual({ gate: "random", draw: arm, on: drawnOn });
				expect(await b.request([])).toEqual([]);
				expect(b.exposures()).toEqual([{ v: 1, feature: "aa_noop", arm, gate: "random", rootSessionId: id, at: expect.any(String) }]);
			});
			test("a gate change between a prompt and its request voids that trigger: no entry for the old arm, and the next prompt records the arm in force", async () => {
				const drawnOff = ids.find(id => !draw(id, "infra805_skill")) as string;
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, rootFile(drawnOff), [], "main", path);
				await b.start();
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf("infra805_skill", skillText));
				writeGates({ infra805_skill: "random" }, 2_000);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: "off", gate: "random", rootSessionId: drawnOff, at: expect.any(String) }]);
			});
			test("a gate going from on to random with the draw on between a prompt and its request sends no text and writes no entry; the next prompt sends the text and records it", async () => {
				const drawnOn = ids.find(id => draw(id, "infra805_skill")) as string;
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, rootFile(drawnOn), [], "main", path);
				await b.start();
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf("infra805_skill", skillText));
				writeGates({ infra805_skill: "random" }, 2_000);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: "on", gate: "random", rootSessionId: drawnOn, at: expect.any(String) }]);
			});
			test("a failed append of a new resolution keeps it proposed: logged once, the text arms send nothing and write nothing until it is recorded, the other features run on, and the next event records it", async () => {
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({ OMP_EXPERIMENT_SKILL_GATE: "on" }, ROOT_FILE, [], "main", path);
				await b.start();
				const delivered = await b.prompt();
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				b.setAppendFailure(customType => customType === ENTRY_TYPE);
				writeGates({ infra805_skill: "on", infra805_selfcheck: "on" }, 2_000);
				expect(await b.request(delivered)).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.errors).toEqual([expect.stringContaining("cannot record their new resolution")]);
				expect(b.records()).toHaveLength(1);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill"]);
				expect((await b.gh())?.block).toBe(true);
				b.setAppendFailure(() => false);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.infra805_selfcheck).toMatchObject({ gate: "on", on: true });
				const next = await b.prompt();
				expect(next).toEqual(deliveryOf("infra805_selfcheck", selfcheckText));
				expect(await b.request([...delivered, ...next])).toEqual(["infra805_skill", "infra805_selfcheck"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck"]);
				expect(b.errors).toHaveLength(1);
			});
			test("a prompt under on, the gate off before its request and on again before an assistant message: no exposure and no text until a new user prompt", async () => {
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, ROOT_FILE, [], "main", path);
				await b.start();
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf("infra805_skill", skillText));
				writeGates({ infra805_skill: "off" }, 2_000);
				expect(await b.request(delivered)).toEqual([]);
				writeGates({ infra805_skill: "on" }, 3_000);
				expect(await b.request(delivered)).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: "on", gate: "on", rootSessionId: ROOT, at: expect.any(String) }]);
			});
		});

		describe("an `experiments` entry that cannot be appended at a resolution", () => {
			test("keeps the text arms closed and is tried again at every later event, logged once; the other features run under the new resolution at once", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
				b.setAppendFailure(customType => customType === ENTRY_TYPE);
				await b.start();
				expect(b.records()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
				expect(await b.request([])).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(b.errors).toEqual([expect.stringContaining("cannot record their new resolution")]);
				expect((await b.gh())?.block).toBe(true);
				b.setAppendFailure(() => false);
				expect(await b.request([])).toEqual([]);
				expect(b.records()).toEqual([resolve(resolveGates(gateFile, { ...TEXT_OFF, OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_SKILL_GATE: "on" }), ROOT)]);
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf("infra805_skill", skillText));
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill"]);
				expect(b.errors).toHaveLength(1);
			});
		});

		describe("a branch read that throws after a successful exposure append", () => {
			test("lets that request carry the text and the entry; at the next request the permit's throw is logged, the feature stops, and the text stays out", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
				await b.start();
				const delivered = await b.prompt();
				b.setBranch(() => {
					throw new Error("branch unavailable");
				});
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill"]);
				expect(b.errors).toEqual([]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.errors).toEqual([expect.stringContaining("infra805_skill failed in the context scrub")]);
				expect(await b.request(delivered)).toEqual([]);
				expect(b.errors).toHaveLength(1);
			});
		});

		describe("a copy of the text that omp's shake altered", () => {
			// omp's shake splices `[shaken ~N tokens — recover: artifact://… (region N)]` over a fenced block of a kept custom message (session-maintenance.ts #shakeElidePlaceholder).
			const SHAKEN = skillText.replace(/```text\n[\s\S]*?\n```/, "[shaken ~780 tokens — recover: artifact://1 (region 1)]");
			const shakenCopy: Delivered = { message: { customType: "infra805_skill", content: SHAKEN, display: false } };
			test("does not count as the text: the scrub keeps it out, and the next user prompt puts the whole text in again", async () => {
				expect(SHAKEN).not.toBe(skillText);
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
				await b.start();
				expect(await b.request(await b.prompt())).toEqual(["infra805_skill"]);
				const kept = b.entries.find(e => e.type === "custom_message" && e.customType === "infra805_skill") as { content: unknown };
				kept.content = SHAKEN;
				expect(await b.request([shakenCopy])).toEqual([]);
				const again = await b.prompt();
				expect(again).toEqual(deliveryOf("infra805_skill", skillText));
				expect(await b.request([shakenCopy, ...again])).toEqual(["infra805_skill"]);
				expect(b.sentCustom()).toEqual([{ role: "custom", customType: "infra805_skill", content: skillText, display: false, timestamp: 2 }]);
				expect(b.exposures()).toHaveLength(1);
			});
		});

		describe("a pre-prompt compaction's request between a trigger and the run's first request", () => {
			test("neither takes the trigger nor writes an entry; the run's first request does both", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on" });
				await b.start();
				const delivered = await b.prompt();
				expect(await b.compactionRequest()).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill"]);
			});
			test("in a Legion pane, leaves the excluded entry for the run's first request", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_AA_NOOP: "on", LEGION_ROLE: "architect" });
				await b.start();
				await b.prompt();
				await b.compactionRequest();
				expect(b.excluded()).toEqual([]);
				await b.request([]);
				expect(b.excluded().map(e => e.feature)).toEqual(["aa_noop"]);
			});
		});

		describe("only a user prompt (architect ruling 48e7077f, item 2) triggers the text and the exposure", () => {
			const ON = { OMP_EXPERIMENT_AA_NOOP: "on", OMP_EXPERIMENT_INFRA805_SKILL: "on" };
			test("a typed user prompt sends the text and records both features", async () => {
				const b = await bindArms(ON);
				await b.start();
				const delivered = await b.prompt();
				expect(await b.request(delivered, { role: "user", content: "Design the cache.", attribution: "user" })).toEqual(["infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill"]);
			});
			test("a user-invoked skill message sends the text and records both features", async () => {
				const b = await bindArms(ON);
				await b.start();
				const delivered = await b.prompt();
				const skillPrompt = { role: "custom", customType: "skill-prompt", content: "Run the review skill.", attribution: "user", display: true };
				expect(await b.request(delivered, skillPrompt)).toEqual(["skill-prompt", "infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill"]);
			});
			test.each([
				["a synthetic prompt", { role: "developer", content: "Continue.", attribution: "agent", synthetic: true }],
				["a synthetic user message", { role: "user", content: "Continue.", synthetic: true }],
				["an agent-attributed user message", { role: "user", content: "From the parent agent.", attribution: "agent" }],
				["a skill message the agent loaded", { role: "custom", customType: "skill-prompt", content: "Autoloaded.", attribution: "agent", display: true }],
			])("%s sends no text and records nothing; a later typed prompt sends the kept text and records it", async (_label, input) => {
				const b = await bindArms(ON);
				await b.start();
				const delivered = await b.prompt();
				expect(delivered).toEqual(deliveryOf("infra805_skill", skillText));
				// The first request decides the trigger, and a later request of the same run still sends no text.
				expect(await b.request(delivered, input)).not.toContain("infra805_skill");
				expect(await b.request(delivered, input)).not.toContain("infra805_skill");
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(delivered)).toEqual(["infra805_skill"]);
				expect(b.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill"]);
			});
			test("a context that keeps the text with no exposure entry and no trigger sends none of it", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, [KEPT_SKILL]);
				await b.start();
				expect(await b.request(deliveryOf("infra805_skill", skillText))).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(deliveryOf("infra805_skill", skillText))).toEqual(["infra805_skill"]);
				expect(b.exposures()).toHaveLength(1);
			});
			test.each([
				["a synthetic prompt", { role: "developer", content: "Continue.", attribution: "agent", synthetic: true }],
				["an agent-attributed user message", { role: "user", content: "From the parent agent.", attribution: "agent" }],
			])("an owed entry whose text a pre-prompt compaction dropped stays owed through %s that adds a fresh copy: no text and no entry until the next typed prompt, which sends the text and writes one entry", async (_label, input) => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
				await b.start();
				expect(await b.prompt()).toEqual(deliveryOf("infra805_skill", skillText));
				// The pre-prompt compaction drops the text: the run's first request holds the typed prompt and not the text.
				b.entries.push({ type: "compaction" });
				expect(await b.request([])).toEqual([]);
				expect(b.exposures()).toEqual([]);
				const fresh = await b.prompt();
				expect(fresh).toEqual(deliveryOf("infra805_skill", skillText));
				expect(await b.request(fresh, input)).toEqual([]);
				expect(await b.request(fresh, input)).toEqual([]);
				expect(b.exposures()).toEqual([]);
				expect(await b.prompt()).toEqual([]);
				expect(await b.request(fresh)).toEqual(["infra805_skill"]);
				expect(b.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: "on", gate: "on", rootSessionId: ROOT, at: expect.any(String) }]);
			});
			test("an owed entry alone lets no text out: a compaction request that holds a whole copy sends none of it", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, [KEPT_SKILL]);
				await b.start();
				expect(await b.prompt()).toEqual([]);
				// The run's first request lacks the text, so the entry stays owed.
				expect(await b.request([])).toEqual([]);
				expect(await b.compactionRequest(deliveryOf("infra805_skill", skillText))).toEqual([]);
				expect(b.exposures()).toEqual([]);
			});
		});
	});
});
