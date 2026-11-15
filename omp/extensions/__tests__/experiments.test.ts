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

	/** The real entry bound to a fake `pi`, with every gate set from `env`, for a session whose file is `sessionFile`. Its branch is its entries unless `setBranch` replaces it, and its read of the session file is its entries unless `setEntries` replaces it. The text arms follow `gatesPath` when one is given, else the committed gates.json, which no test writes. */
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
		let readEntries: () => unknown[] = () => entries;
		const ctx = {
			agent: { kind },
			sessionManager: { getSessionFile: () => sessionFile, getSessionId: () => ROOT, getEntries: () => readEntries(), getBranch: () => branch() },
		};
		const emit = async (event: string, payload: unknown) => {
			let out: unknown;
			for (const handler of handlers.get(event) ?? []) out = (await handler(payload, ctx)) ?? out;
			return out;
		};
		type Message = { role: string; customType?: string; content?: unknown; [key: string]: unknown };
		/** One request: every context handler over `input`, each getting what the one before it returned, as omp runs them. Returns the messages that go out. */
		const send = async (input: Message[]): Promise<Message[]> => {
			let messages = input;
			for (const handler of handlers.get("context") ?? []) {
				const result = (await handler({ type: "context", messages }, ctx)) as { messages?: Message[] } | undefined;
				if (result?.messages) messages = result.messages;
			}
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
			setEntries: (fn: typeof readEntries) => (readEntries = fn),
			setAppendFailure: (fn: typeof appendFails) => (appendFails = fn),
			exposures: () => entries.filter(e => e.customType === EXPOSURE_ENTRY_TYPE).map(e => e.data as ExposureRecord),
			excluded: () => entries.filter(e => e.customType === EXCLUDED_ENTRY_TYPE).map(e => e.data as ExcludedRecord),
			/** Every before_agent_start handler of one prompt, in order; a wake's reason is `revive`. */
			prompt: (reason: "prompt" | "revive" = "prompt") => emit("before_agent_start", { type: "before_agent_start", reason, prompt: "Design the cache.", systemPrompt: ["You are omp."] }),
			send,
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
		const TEXTS: ReadonlyArray<[Feature, string]> = [
			["infra805_skill", skillText],
			["infra805_selfcheck", selfcheckText],
		];
		/** The three gates of this block start off in every session here, whatever gates.json commits, so a change of gates.json (S8 step 4) changes no test. */
		const TEXT_OFF = { OMP_EXPERIMENT_AA_NOOP: "off", OMP_EXPERIMENT_INFRA805_SKILL: "off", OMP_EXPERIMENT_INFRA805_SELFCHECK: "off" };
		const bindArms: typeof bind = (env, ...rest) => bind({ ...TEXT_OFF, ...env }, ...rest);
		type Message = { role: string; [key: string]: unknown };
		/** The message that carries the text of `feature` in a request. */
		const textMessage = (feature: Feature, text: string): Message => ({ role: "custom", customType: feature, content: text, display: false, attribution: "user", timestamp: 0 });
		const SKILL = textMessage("infra805_skill", skillText);
		const SELFCHECK = textMessage("infra805_selfcheck", selfcheckText);
		/** A run as its requests carry it: a typed prompt, a reply that calls a tool, the tool's result, the closing reply, and a wake from a peer. */
		const PROMPT: Message = { role: "user", content: "Design the cache.", attribution: "user", timestamp: 100 };
		const CALL: Message = { role: "assistant", content: [{ type: "toolCall", id: "t1", name: "read", arguments: { path: "a" } }], timestamp: 101 };
		const RESULT: Message = { role: "toolResult", toolCallId: "t1", toolName: "read", content: [{ type: "text", text: "a" }], timestamp: 102 };
		const REPLY: Message = { role: "assistant", content: [{ type: "text", text: "Done." }], timestamp: 103 };
		const WAKE: Message = { role: "custom", customType: "envoy-wake", content: "A message from a peer.", display: true, attribution: "agent", timestamp: 104 };
		/** A compaction summary that omp sends as text, and one that it replays as a native Anthropic compaction block. */
		const SUMMARY: Message = { role: "compactionSummary", summary: "Earlier work.", tokensBefore: 1000, timestamp: 90 };
		const NATIVE: Message = { ...SUMMARY, providerPayload: { type: "anthropicCompaction" } };
		/** Copies of the texts as an earlier version of this extension stored them, one altered by omp's shake (`[shaken ~N tokens …]` over a fenced block). */
		const SHAKEN = skillText.replace(/```text\n[\s\S]*?\n```/, "[shaken ~780 tokens — recover: artifact://1 (region 1)]");
		const COPIES: Message[] = [
			{ role: "custom", customType: "infra805_skill", content: skillText, display: false, attribution: "user", timestamp: 5 },
			{ role: "custom", customType: "infra805_skill", content: SHAKEN, display: false, attribution: "user", timestamp: 6 },
			{ role: "custom", customType: "infra805_selfcheck", content: selfcheckText, display: false, attribution: "user", timestamp: 7 },
		];

		test.each(TEXTS)("%s in the on arm puts its whole text once into every request at the same early position: a prompt, tool turns, a shake of earlier messages, a wake; nothing of it is stored", async (feature, text) => {
			expect(SHAKEN).not.toBe(skillText);
			const drawnOn = ids.find(id => draw(id, feature)) as string;
			for (const [gate, file] of [["on", ROOT_FILE], ["random", rootFile(drawnOn)]] as const) {
				const b = await bindArms({ [envName(feature)]: gate }, file);
				await b.start();
				expect(await b.prompt()).toBeUndefined();
				const placed = textMessage(feature, text);
				const shaken: Message = { ...RESULT, content: [{ type: "text", text: "[shaken ~120 tokens — recover: artifact://2 (region 1)]" }] };
				for (const request of [[PROMPT], [PROMPT, CALL, RESULT], [PROMPT, CALL, RESULT, REPLY], [PROMPT, CALL, shaken, REPLY]]) {
					expect(await b.send(request)).toEqual([placed, ...request]);
				}
				expect(await b.prompt("revive")).toBeUndefined();
				const woken = [PROMPT, CALL, RESULT, REPLY, WAKE];
				expect(await b.send(woken)).toEqual([placed, ...woken]);
				expect(JSON.stringify(b.entries)).not.toContain(JSON.stringify(text));
			}
		});
		test("after a compaction the texts go before a summary that omp sends as text, and after a native compaction block with the assistant turn folded into it, the same in each request until the next compaction", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" });
			await b.start();
			const text = [SUMMARY, PROMPT, CALL, RESULT];
			expect(await b.send(text)).toEqual([SKILL, SELFCHECK, ...text]);
			expect(await b.send([...text, REPLY])).toEqual([SKILL, SELFCHECK, ...text, REPLY]);
			expect(await b.send([NATIVE, PROMPT])).toEqual([NATIVE, SKILL, SELFCHECK, PROMPT]);
			expect(await b.send([NATIVE, CALL, RESULT, PROMPT])).toEqual([NATIVE, CALL, RESULT, SKILL, SELFCHECK, PROMPT]);
			expect(await b.send([NATIVE, CALL, RESULT, PROMPT, REPLY])).toEqual([NATIVE, CALL, RESULT, SKILL, SELFCHECK, PROMPT, REPLY]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_selfcheck"]);
		});
		test("with both INFRA-805 arms on, every request carries the skill and then the self-check, stored copies of either taken out, and each feature writes its own exposure entry", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" });
			await b.start();
			expect(await b.send([COPIES[2], PROMPT, COPIES[0]])).toEqual([SKILL, SELFCHECK, PROMPT]);
			expect(await b.send([COPIES[1], PROMPT, CALL, COPIES[2], RESULT])).toEqual([SKILL, SELFCHECK, PROMPT, CALL, RESULT]);
			expect(b.exposures().map(e => [e.feature, e.arm])).toEqual([["infra805_skill", "on"], ["infra805_selfcheck", "on"]]);
		});
		test("a subagent draws from its root, records its own assignment and exposure in its own file, and each of its requests carries the text of its root's arm, a wake's included", async () => {
			const drawnOn = ids.find(id => draw(id, "infra805_skill")) as string;
			const drawnOff = ids.find(id => !draw(id, "infra805_skill")) as string;
			for (const [root, on] of [[drawnOn, true], [drawnOff, false]] as const) {
				const sub = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "random" }, rootFile(root).replace(/\.jsonl$/, "/Worker.jsonl"), [], "sub");
				await sub.start();
				expect(sub.records()[0].features.infra805_skill).toEqual({ gate: "random", draw: on ? "on" : "off", on });
				const turn = [PROMPT, CALL, RESULT];
				expect(await sub.send(turn)).toEqual(on ? [SKILL, ...turn] : turn);
				await sub.prompt("revive");
				const woken = [...turn, REPLY, WAKE];
				expect(await sub.send(woken)).toEqual(on ? [SKILL, ...woken] : woken);
				expect(sub.exposures()).toEqual([{ v: 1, feature: "infra805_skill", arm: on ? "on" : "off", gate: "random", rootSessionId: root, at: expect.any(String) }]);
			}
		});
		test.each([...TEXTS, ["aa_noop", undefined] as const])("%s writes one exposure entry at the first request after its resolution is recorded, in both arms of an on or random gate, and none again under that resolution", async (feature, text) => {
			const drawnOn = ids.find(id => draw(id, feature)) as string;
			const drawnOff = ids.find(id => !draw(id, feature)) as string;
			const cases = [["on", ROOT_FILE, ROOT, "on"], ["random", rootFile(drawnOn), drawnOn, "on"], ["random", rootFile(drawnOff), drawnOff, "off"]] as const;
			for (const [gate, file, root, arm] of cases) {
				const b = await bindArms({ [envName(feature)]: gate }, file);
				await b.start();
				await b.prompt();
				expect(b.exposures()).toEqual([]);
				// The request at which the on arm's entry is written carries the text.
				expect(await b.send([PROMPT])).toEqual(arm === "on" && text !== undefined ? [textMessage(feature, text), PROMPT] : [PROMPT]);
				expect(b.exposures()).toEqual([{ v: 1, feature, arm, gate, rootSessionId: root, at: expect.any(String) }]);
				const { at } = b.exposures()[0];
				expect(new Date(at).toISOString()).toBe(at);
				expect(Math.abs(Date.parse(at) - Date.now())).toBeLessThan(60_000);
				await b.send([PROMPT, CALL, RESULT]);
				await b.prompt();
				await b.send([PROMPT, CALL, RESULT, REPLY]);
				expect(b.exposures()).toHaveLength(1);
			}
		});
		test("a resumed session gets no second exposure entry under the same recorded resolution, and gets one at its first request when its file holds none after that resolution", async () => {
			const ended = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" });
			await ended.start();
			const resumed = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, ended.entries);
			await resumed.start();
			expect(resumed.records()).toHaveLength(1);
			expect(await resumed.send([PROMPT])).toEqual([SKILL, PROMPT]);
			expect(resumed.exposures()).toHaveLength(1);
			const again = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on" }, ROOT_FILE, resumed.entries);
			await again.start();
			expect(await again.send([PROMPT, CALL, RESULT])).toEqual([SKILL, PROMPT, CALL, RESULT]);
			expect(again.exposures()).toHaveLength(1);
		});
		test("no request carries a text, and every stored copy is taken out, while the arm is not on: before the first resolution, gate off, draw off", async () => {
			const request = [COPIES[0], PROMPT, COPIES[1], CALL, RESULT, COPIES[2]];
			const without = [PROMPT, CALL, RESULT];
			const off = await bindArms({});
			expect(await off.send(request)).toEqual(without);
			await off.start();
			expect(await off.send(request)).toEqual(without);
			expect(await off.send([NATIVE, CALL, RESULT, ...request])).toEqual([NATIVE, CALL, RESULT, ...without]);
			expect(off.exposures()).toEqual([]);
			const drawnOff = ids.find(id => !draw(id, "infra805_skill") && !draw(id, "infra805_selfcheck")) as string;
			const control = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "random", OMP_EXPERIMENT_INFRA805_SELFCHECK: "random" }, rootFile(drawnOff));
			await control.start();
			expect(await control.send(request)).toEqual(without);
			expect(await control.send([PROMPT])).toEqual([PROMPT]);
			expect(control.exposures().map(e => [e.feature, e.arm])).toEqual([["infra805_skill", "off"], ["infra805_selfcheck", "off"]]);
		});
		test("aa_noop changes no request, prompt or tool call in either arm: its two arms differ only in the arm of its exposure entry", async () => {
			const run = async (root: string) => {
				const env = { ...allOff, OMP_EXPERIMENT_SKILL_GATE: "on", OMP_EXPERIMENT_RULES_REFRESH: "on", OMP_EXPERIMENT_BASH_GATE_LEADING: "on", OMP_EXPERIMENT_AA_NOOP: "random" };
				const b = await bindArms(env, rootFile(root));
				await b.start();
				for (const request of [[PROMPT], [PROMPT, CALL, RESULT, REPLY, WAKE], [SUMMARY, PROMPT, CALL, RESULT], [NATIVE, CALL, RESULT, PROMPT]]) {
					const before = structuredClone(request);
					expect(await b.send(request)).toBe(request);
					expect(request).toEqual(before);
				}
				const seen = [await b.prompt(), await b.gh(), await b.bash("ls d && cat f"), await b.bash("cat f")];
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
		test("a failed exposure append stops its feature, logged: that request and every later one carry none of its text, the other features run on, and the next resolution starts it again", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
			await b.start();
			b.setAppendFailure((customType, data) => customType === EXPOSURE_ENTRY_TYPE && (data as ExposureRecord).feature === "infra805_skill");
			expect(await b.send([PROMPT])).toEqual([SELFCHECK, PROMPT]);
			expect(b.errors).toEqual([expect.stringContaining("infra805_skill failed in context")]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			b.setAppendFailure(() => false);
			expect(await b.send([COPIES[0], PROMPT, CALL, RESULT])).toEqual([SELFCHECK, PROMPT, CALL, RESULT]);
			await b.prompt();
			expect(await b.send([PROMPT, CALL, RESULT, REPLY])).toEqual([SELFCHECK, PROMPT, CALL, RESULT, REPLY]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			expect((await b.gh())?.block).toBe(true);
			await b.start();
			expect(await b.send([PROMPT])).toEqual([SKILL, SELFCHECK, PROMPT]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck", "infra805_skill"]);
			expect(b.errors).toHaveLength(1);
		});
		test("a text arm whose handler throws stops, logged: no request carries its text from that one on, and the other features run on", async () => {
			const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
			await b.start();
			// aa_noop is off here, so the first reader of the session file at a request is infra805_skill.
			let calls = 0;
			b.setEntries(() => {
				if (calls++ === 0) throw new Error("the session file cannot be read");
				return b.entries;
			});
			expect(await b.send([COPIES[0], PROMPT])).toEqual([SELFCHECK, PROMPT]);
			expect(b.errors).toEqual([expect.stringContaining("infra805_skill failed in context")]);
			expect(await b.send([COPIES[0], PROMPT, CALL, RESULT])).toEqual([SELFCHECK, PROMPT, CALL, RESULT]);
			expect(b.exposures().map(e => e.feature)).toEqual(["infra805_selfcheck"]);
			expect((await b.gh())?.block).toBe(true);
		});
		test.each(LEGION_ENV)("a Legion pane (%s set) gets one excluded entry per feature whose gate is on or random at its first request, no exposure entry and no text, and an empty value is not a Legion pane", async name => {
			const all = { OMP_EXPERIMENT_AA_NOOP: "random", OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_INFRA805_SELFCHECK: "on" };
			const legion = await bindArms({ ...all, [name]: "x" });
			await legion.start();
			expect(legion.records()[0].features.infra805_skill.on).toBe(true);
			await legion.prompt();
			expect(legion.excluded()).toEqual([]);
			expect(await legion.send([COPIES[0], PROMPT, COPIES[2]])).toEqual([PROMPT]);
			expect(legion.excluded()).toEqual(
				(["aa_noop", "infra805_skill", "infra805_selfcheck"] as const).map(feature => ({ v: 1, feature, reason: "legion_pane", rootSessionId: ROOT, at: expect.any(String) })),
			);
			expect(await legion.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
			expect(legion.exposures()).toEqual([]);
			expect(legion.excluded()).toHaveLength(3);
			const resumed = await bindArms({ ...all, [name]: "x" }, ROOT_FILE, legion.entries);
			await resumed.start();
			expect(await resumed.send([PROMPT])).toEqual([PROMPT]);
			expect(resumed.excluded()).toHaveLength(3);
			expect(resumed.exposures()).toEqual([]);
			const skillOff = await bindArms({ ...all, OMP_EXPERIMENT_INFRA805_SKILL: "off", [name]: "x" });
			await skillOff.start();
			await skillOff.send([PROMPT]);
			expect(skillOff.excluded().map(e => e.feature)).toEqual(["aa_noop", "infra805_selfcheck"]);
			const outside = await bindArms({ ...all, [name]: "" });
			await outside.start();
			expect(await outside.send([PROMPT])).toEqual([SKILL, SELFCHECK, PROMPT]);
			expect(outside.exposures().map(e => e.feature)).toEqual(["aa_noop", "infra805_skill", "infra805_selfcheck"]);
			expect(outside.excluded()).toEqual([]);
		});

		describe("an `experiments` entry that cannot be appended at a resolution", () => {
			test("keeps every text out and writes no exposure until it is recorded, is tried again at every later event and logged once, and the other features run under the new resolution at once", async () => {
				const b = await bindArms({ OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_SKILL_GATE: "on" });
				b.setAppendFailure(customType => customType === ENTRY_TYPE);
				await b.start();
				expect(b.records()).toEqual([]);
				await b.prompt();
				expect(await b.send([COPIES[0], PROMPT])).toEqual([PROMPT]);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
				expect(b.exposures()).toEqual([]);
				expect(b.errors).toEqual([expect.stringContaining("cannot record their new resolution")]);
				expect((await b.gh())?.block).toBe(true);
				b.setAppendFailure(() => false);
				// The entry is recorded first at the next request, and that request carries the text.
				expect(await b.send([PROMPT, CALL, RESULT, REPLY])).toEqual([SKILL, PROMPT, CALL, RESULT, REPLY]);
				expect(b.records()).toEqual([resolve(resolveGates(gateFile, { ...TEXT_OFF, OMP_EXPERIMENT_INFRA805_SKILL: "on", OMP_EXPERIMENT_SKILL_GATE: "on" }), ROOT)]);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill"]);
				expect(b.errors).toHaveLength(1);
			});
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

			test("an off gate written while a session runs takes the text out of the next request, with an `experiments` entry and no new exposure, and the other features keep their load-time gates", async () => {
				writeGates({ infra805_skill: "on", bash_gate_leading: "off" }, 1_000);
				const b = await bind({}, ROOT_FILE, [], "main", path);
				await b.start();
				const [first] = b.records();
				expect(first.features.infra805_skill).toMatchObject({ gate: "on", on: true });
				expect(first.features.bash_gate_leading.gate).toBe(gateFile.bash_gate_leading);
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				expect(b.exposures()).toHaveLength(1);
				writeGates({ infra805_skill: "off", bash_gate_leading: "off" }, 2_000);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.infra805_skill).toMatchObject({ gate: "off", on: false });
				expect(records[1].features.bash_gate_leading).toEqual(first.features.bash_gate_leading);
				await b.prompt();
				expect(await b.send([PROMPT, CALL, RESULT, REPLY])).toEqual([PROMPT, CALL, RESULT, REPLY]);
				expect(b.exposures()).toHaveLength(1);
				expect(b.records()).toHaveLength(2);
			});
			test("a gates.json that cannot be parsed keeps the last good gates and is logged once", async () => {
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, ROOT_FILE, [], "main", path);
				await b.start();
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				writeGates("{ not json", 2_000);
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				await b.prompt();
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				expect(b.errors).toEqual([expect.stringContaining(`the text arms cannot read the gates in ${path}`)]);
				expect(b.records()).toHaveLength(1);
				writeGates({ infra805_skill: "off" }, 3_000);
				expect(await b.send([PROMPT])).toEqual([PROMPT]);
				expect(b.records()).toHaveLength(2);
				expect(b.errors).toHaveLength(1);
			});
			test.each([true, false])("aa_noop going random mid-process (draw on: %p) records an `experiments` entry at the next prompt, and the next request writes an exposure entry with the drawn arm and changes nothing", async drawnOn => {
				const id = ids.find(i => draw(i, "aa_noop") === drawnOn) as string;
				const arm = drawnOn ? "on" : "off";
				writeGates({}, 1_000);
				const b = await bind({}, rootFile(id), [], "main", path);
				await b.start();
				await b.send([PROMPT]);
				expect(b.exposures()).toEqual([]);
				writeGates({ aa_noop: "random" }, 2_000);
				await b.prompt();
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.aa_noop).toEqual({ gate: "random", draw: arm, on: drawnOn });
				expect(b.exposures()).toEqual([]);
				const request = [PROMPT];
				expect(await b.send(request)).toBe(request);
				expect(b.exposures()).toEqual([{ v: 1, feature: "aa_noop", arm, gate: "random", rootSessionId: id, at: expect.any(String) }]);
			});
			test("each recorded resolution under an on or random gate gets its own exposure entry: on, random with the draw on, off, random again, and a change of another feature", async () => {
				const drawnOn = ids.find(id => draw(id, "infra805_skill")) as string;
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, rootFile(drawnOn), [], "main", path);
				await b.start();
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				writeGates({ infra805_skill: "random" }, 2_000);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([SKILL, PROMPT, CALL, RESULT]);
				writeGates({ infra805_skill: "off" }, 3_000);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
				writeGates({ infra805_skill: "random" }, 4_000);
				expect(await b.send([PROMPT, CALL, RESULT, REPLY])).toEqual([SKILL, PROMPT, CALL, RESULT, REPLY]);
				writeGates({ infra805_skill: "random", aa_noop: "random" }, 5_000);
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				await b.send([PROMPT, CALL, RESULT]);
				expect(b.records().map(r => r.features.infra805_skill.gate)).toEqual(["on", "random", "off", "random", "random"]);
				const skillExposures = b.exposures().filter(e => e.feature === "infra805_skill");
				expect(skillExposures.map(e => [e.arm, e.gate])).toEqual([["on", "on"], ["on", "random"], ["on", "random"], ["on", "random"]]);
			});
			test("a gate going from on to random with the draw off takes the text out from the next request and records an exposure entry in the off arm", async () => {
				const drawnOff = ids.find(id => !draw(id, "infra805_skill")) as string;
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({}, rootFile(drawnOff), [], "main", path);
				await b.start();
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				writeGates({ infra805_skill: "random" }, 2_000);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
				expect(b.exposures().map(e => [e.arm, e.gate])).toEqual([["on", "on"], ["off", "random"]]);
			});
			test("a failed append of a new resolution keeps it proposed: logged once, the text arms write nothing and no request carries a text until it is recorded, the other features run on, and the next event records it", async () => {
				writeGates({ infra805_skill: "on" }, 1_000);
				const b = await bind({ OMP_EXPERIMENT_SKILL_GATE: "on" }, ROOT_FILE, [], "main", path);
				await b.start();
				expect(await b.send([PROMPT])).toEqual([SKILL, PROMPT]);
				b.setAppendFailure(customType => customType === ENTRY_TYPE);
				writeGates({ infra805_skill: "on", infra805_selfcheck: "on" }, 2_000);
				expect(await b.send([PROMPT])).toEqual([PROMPT]);
				await b.prompt();
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([PROMPT, CALL, RESULT]);
				expect(b.errors).toEqual([expect.stringContaining("cannot record their new resolution")]);
				expect(b.records()).toHaveLength(1);
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill"]);
				expect((await b.gh())?.block).toBe(true);
				b.setAppendFailure(() => false);
				expect(await b.send([PROMPT, CALL, RESULT])).toEqual([SKILL, SELFCHECK, PROMPT, CALL, RESULT]);
				const records = b.records();
				expect(records).toHaveLength(2);
				expect(records[1].features.infra805_selfcheck).toMatchObject({ gate: "on", on: true });
				expect(b.exposures().map(e => e.feature)).toEqual(["infra805_skill", "infra805_skill", "infra805_selfcheck"]);
				expect(b.errors).toHaveLength(1);
			});
		});
	});
});
