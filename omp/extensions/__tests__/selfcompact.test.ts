import { afterEach, beforeEach, describe, expect, test, vi } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import extension, { parseRubric } from "../selfcompact";

type Pi = Parameters<typeof extension>[0];
type Handler = (event: unknown, ctx: unknown) => void | Promise<void>;
type Kind = "main" | "sub";

const COMPRESS = 'C1: Y--"tests pass"\nC2: Y--1. a "x" | 2. b "y" | 3. c "z"\nC3: Y--new file:line\nN1: N--saw new output';
const CONTINUE = "C1: N--mid\nC2: N--x\nC3: N--x\nN1: N--x";
/** The four billing buckets the probe records, plus the cost the spend cap counts. */
const usage = { input: 12, output: 40, cacheRead: 61_000, cacheWrite: 0 };
const reply = (text: string) => ({
	replyText: text,
	assistantMessage: {
		role: "assistant",
		provider: "anthropic",
		model: "claude-sonnet-5",
		usage: { ...usage, totalTokens: 61_052, cost: { input: 0, output: 0.0006, cacheRead: 0.0183, cacheWrite: 0, total: 0.0189 } },
		stopReason: "stop",
		content: [],
	},
});
/** A request context holding `turns` completed assistant turns (a tool result closes the last one). */
const history = (turns: number) => {
	const messages: Array<{ role: string }> = [{ role: "user" }];
	for (let i = 0; i < turns; i++) messages.push({ role: "assistant" }, { role: "toolResult" });
	return messages;
};

let tmp: string;
beforeEach(() => {
	tmp = mkdtempSync(path.join(tmpdir(), "selfcompact-"));
	process.env.OMP_SELFCOMPACT = "1";
	process.env.OMP_SELFCOMPACT_LOG = path.join(tmp, "probes.jsonl");
});
afterEach(() => {
	delete process.env.OMP_SELFCOMPACT;
	delete process.env.OMP_SELFCOMPACT_LOG;
	rmSync(tmp, { recursive: true, force: true });
});

type CompactCall = { onComplete?: (r: unknown) => void; onError?: (e: Error) => void };
/** One session binding of the extension, driven through a fake `pi` and a scripted `ctx`. */
interface Binding {
	handlers: Map<string, Handler>;
	entries: Array<[string, Record<string, unknown>]>;
	probeCalls: unknown[];
	compactCalls: unknown[];
	/** Messages the extension passed to `pi.logger.warn`: what the host would show as a warning line. */
	warnings: string[];
	/** One provider request about to be sent with `turns` assistant turns in its context. */
	request: (turns: number) => void | Promise<void>;
	/** The `context` hook for an arbitrary message list, e.g. a side turn's snapshot. */
	context: (messages: Array<{ role: string }>) => void | Promise<void>;
	compacted: () => void | Promise<void>;
	shakeEnd: (outcome?: Record<string, unknown>) => void | Promise<void>;
	/** A session lifecycle event with no payload the extension reads (`session_switch`, `session_branch`, `session_tree`). */
	session: (event: string) => void | Promise<void>;
}

function bind(opts: {
	kind?: Kind;
	tokens?: number;
	replies?: string[];
	probe?: (options: unknown) => Promise<unknown>;
	compact?: (options: CompactCall) => Promise<void>;
	usageThrows?: boolean;
} = {}): Binding {
	const handlers = new Map<string, Handler>();
	const entries: Array<[string, Record<string, unknown>]> = [];
	const probeCalls: unknown[] = [];
	const compactCalls: unknown[] = [];
	const warnings: string[] = [];
	const replies = [...(opts.replies ?? [COMPRESS])];
	const pi = {
		on: (event: string, handler: Handler) => handlers.set(event, handler),
		appendEntry: (customType: string, data: Record<string, unknown>) => entries.push([customType, data]),
		logger: { warn: (message: string) => warnings.push(message), info: () => {}, debug: () => {}, error: () => {} },
	} as unknown as Pi;
	extension(pi);
	const ctx = {
		agent: { kind: opts.kind ?? "main" },
		getContextUsage: () => {
			if (opts.usageThrows) throw new Error("usage unavailable");
			const tokens = opts.tokens ?? 60_000;
			return { tokens, contextWindow: 200_000, percent: (tokens / 200_000) * 100 };
		},
		runEphemeralTurn: async (options: unknown) => {
			probeCalls.push(options);
			if (opts.probe) return opts.probe(options);
			return reply(replies.shift() ?? COMPRESS);
		},
		compact: async (options: CompactCall) => {
			compactCalls.push(options);
			if (opts.compact) return opts.compact(options);
			options.onComplete?.({ summary: "s", firstKeptEntryId: "e9", tokensBefore: 60_000 });
		},
	};
	const emit = (event: string, payload: unknown) => handlers.get(event)?.(payload, ctx);
	const context = (messages: Array<{ role: string }>) => emit("context", { type: "context", messages });
	return {
		handlers,
		entries,
		probeCalls,
		compactCalls,
		warnings,
		request: (turns: number) => context(history(turns)),
		context,
		compacted: () => emit("session_compact", { compactionEntry: {}, fromExtension: false }),
		shakeEnd: (outcome: Record<string, unknown> = {}) =>
			emit("auto_compaction_end", { action: "shake", result: undefined, aborted: false, willRetry: false, ...outcome }),
		session: (event: string) => emit(event, { type: event }),
	};
}

/** `n` consecutive requests, each awaited as the loop awaits the context hook; turns grow with them. */
async function requests(b: Binding, n: number, firstTurns = 1) {
	for (let i = 0; i < n; i++) await b.request(firstTurns + i);
}

/** Runs every queued microtask, so each detached promise chain the extension started has finished. */
async function drain() {
	const { promise, resolve } = Promise.withResolvers<void>();
	setImmediate(resolve);
	await promise;
}

describe("arming", () => {
	test("without OMP_SELFCOMPACT=1 the factory registers no handler, so a session pays nothing", () => {
		delete process.env.OMP_SELFCOMPACT;
		expect(bind().handlers.size).toBe(0);
	});
	test("a subagent is never probed", async () => {
		const b = bind({ kind: "sub" });
		await requests(b, 6);
		expect(b.probeCalls).toHaveLength(0);
	});
});

describe("pre-gates", () => {
	test("no probe under three completed turns, below 40k tokens, or on a request whose context has no assistant turn", async () => {
		const early = bind();
		await early.request(2);
		expect(early.probeCalls).toHaveLength(0);
		const small = bind({ tokens: 39_999 });
		await requests(small, 5, 3);
		expect(small.probeCalls).toHaveLength(0);
		const steering = bind();
		await steering.request(0);
		await steering.request(0);
		expect(steering.probeCalls).toHaveLength(0);
	});
	test("a context that ends in its own prompt, not in tool results (a side turn: /btw, /omfg, the idle recap; a handoff), neither counts nor probes", async () => {
		const b = bind();
		await b.context([...history(5), { role: "developer" }, { role: "user" }]); // runEphemeralTurn's snapshot
		await b.context([...history(5), { role: "user" }]); // handoff generation
		expect(b.probeCalls).toHaveLength(0);
		await b.request(5); // the next main-loop request probes
		expect(b.probeCalls).toHaveLength(1);
		// Neither side context was counted: this is the session's first counted request.
		expect(b.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ request: 1 });
	});
	test("probes are at least two requests apart and one fire closes the window until a compaction resets it", async () => {
		const b = bind({ replies: [CONTINUE, COMPRESS, COMPRESS] });
		await b.request(3); // probe 1: continue
		await b.request(4); // period gate
		expect(b.probeCalls).toHaveLength(1);
		await b.request(5); // probe 2: compress → fire
		expect(b.compactCalls).toHaveLength(1);
		await requests(b, 4, 6); // cap: no more probes this window
		expect(b.probeCalls).toHaveLength(2);
		await b.compacted();
		await requests(b, 2, 3); // after the compaction the context holds the retained tail again
		expect(b.probeCalls).toHaveLength(3);
	});
	test("a successful shake resets the window; an aborted, skipped or fallen-through one does not", async () => {
		const b = bind();
		await b.request(3);
		expect(b.compactCalls).toHaveLength(1);
		await b.shakeEnd({ skipped: true });
		await b.shakeEnd({ aborted: true });
		await b.shakeEnd({ errorMessage: "shake would not reduce context" });
		await requests(b, 3, 4);
		expect(b.probeCalls).toHaveLength(1);
		await b.shakeEnd();
		await requests(b, 2, 4);
		expect(b.probeCalls).toHaveLength(2);
	});
	for (const event of ["session_switch", "session_branch", "session_tree"]) {
		test(`${event} (a new, resumed or forked session; a branch; a tree move) starts a fresh window`, async () => {
			const b = bind();
			await b.request(3); // probe → fire: this window is closed until a compaction the extension did not cause
			await drain();
			await requests(b, 2, 4);
			expect(b.probeCalls).toHaveLength(1);
			await b.session(event);
			await b.request(3); // the first counted request of the next session
			expect(b.probeCalls).toHaveLength(2);
		});
	}
});

describe("parseRubric", () => {
	test("fires only on Y,Y,Y,N in order and treats everything else as continue", () => {
		expect(parseRubric(COMPRESS).verdict).toBe("compress");
		expect(parseRubric(COMPRESS.replace("N1: N", "N1: Y")).verdict).toBe("continue");
		expect(parseRubric(COMPRESS.replace("C2: Y", "C2: N")).verdict).toBe("continue");
		expect(parseRubric(`Sure, here is my assessment:\n${COMPRESS}`).reason).toMatch(/four lines/);
		expect(parseRubric(COMPRESS.split("\n").slice(0, 3).join("\n")).reason).toMatch(/four lines/);
		expect(parseRubric(COMPRESS.replace("C3:", "C4:")).reason).toMatch(/order/);
		expect(parseRubric("C1: Y --x\nC2: Y --x\nC3: Y --x\nN1: N --x").verdict).toBe("compress");
	});
});

describe("firing", () => {
	test("a compress verdict calls compact once, with no mode and no suppressContinuation, and the hook returns before it settles", async () => {
		const pending = Promise.withResolvers<void>();
		const b = bind({ compact: () => pending.promise });
		await b.request(3); // resolves although the compaction has not settled
		expect(b.compactCalls).toHaveLength(1);
		const options = b.compactCalls[0];
		expect(options).not.toHaveProperty("mode");
		expect(options).not.toHaveProperty("suppressContinuation");
		pending.resolve();
	});
	test("the probe request keeps the tool catalog and caps output", async () => {
		const b = bind();
		await b.request(3);
		const options = b.probeCalls[0];
		expect(options).toMatchObject({ maxTokens: expect.any(Number), conversationKey: expect.any(String) });
		expect(options).not.toHaveProperty("tools");
	});
	test("a model that rejects maxTokens is retried once without the cap and the record says so", async () => {
		let calls = 0;
		const b = bind({
			probe: async (options: unknown) => {
				calls++;
				if (options !== null && typeof options === "object" && "maxTokens" in options && options.maxTokens !== undefined) {
					throw new Error("Model anthropic/x does not support maxTokens for ephemeral turns. Omit the cap or use a model that supports output limits.");
				}
				return reply(COMPRESS);
			},
		});
		await b.request(3);
		expect(calls).toBe(2);
		const probe = b.entries.find(([t]) => t === "selfcompact-probe")?.[1];
		expect(probe?.capped).toBe(false);
		expect(b.compactCalls).toHaveLength(1);
	});
	test("capped is true only when the capped call produced the verdict", async () => {
		const retryFails = bind({
			probe: async (options: unknown) => {
				if (options !== null && typeof options === "object" && "maxTokens" in options && options.maxTokens !== undefined) {
					throw new Error("Model anthropic/x does not support maxTokens for ephemeral turns. Omit the cap or use a model that supports output limits.");
				}
				throw new Error("side turn aborted");
			},
		});
		await retryFails.request(3);
		expect(retryFails.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ capped: false, error: "side turn aborted" });
		const cappedCallFails = bind({
			probe: async () => {
				throw new Error("boom");
			},
		});
		await cappedCallFails.request(3);
		expect(cappedCallFails.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ capped: false, error: "boom" });
	});
});

describe("records", () => {
	test("each probe and fire is appended to the session and to OMP_SELFCOMPACT_LOG with the fields the report reads", async () => {
		const b = bind();
		await b.request(3);
		const probe = b.entries.find(([t]) => t === "selfcompact-probe")?.[1];
		expect(probe).toMatchObject({
			v: 1,
			turn: 3,
			request: 1,
			tokens: 60_000,
			verdict: "compress",
			answers: { C1: "Y", C2: "Y", C3: "Y", N1: "N" },
			usage,
			cost: 0.0189,
			model: "anthropic/claude-sonnet-5",
			stopReason: "stop",
			capped: true,
		});
		expect(typeof probe?.probeMs).toBe("number");
		const fire = b.entries.find(([t]) => t === "selfcompact-fire")?.[1];
		expect(fire).toMatchObject({ v: 1, turn: 3, ok: true, tokensBefore: 60_000, firstKeptEntryId: "e9", summary: "s" });
		const lines = readFileSync(process.env.OMP_SELFCOMPACT_LOG as string, "utf8").trim().split("\n").map(l => JSON.parse(l));
		expect(lines.map(l => l.type)).toEqual(["selfcompact-probe", "selfcompact-fire"]);
		expect(lines[0]).toMatchObject({ verdict: "compress", tokens: 60_000, cost: 0.0189 });
	});
	test("a reply whose usage has no cost still fires, and its probe record carries no cost", async () => {
		const b = bind({
			probe: async () => {
				const costless = reply(COMPRESS);
				costless.assistantMessage.usage = { ...usage, totalTokens: 61_052 } as typeof costless.assistantMessage.usage;
				return costless;
			},
		});
		await b.request(3);
		expect(b.compactCalls).toHaveLength(1);
		const probe = b.entries.find(([t]) => t === "selfcompact-probe")?.[1];
		expect(probe).toMatchObject({ verdict: "compress", usage });
		expect(probe).not.toHaveProperty("cost", expect.anything());
		expect(b.entries.map(([t]) => t)).toEqual(["selfcompact-probe", "selfcompact-fire"]);
	});
});

describe("concurrency", () => {
	test("a request that arrives while a probe is pending, and the probe's own re-entrant context hook, are no-ops", async () => {
		const answer = Promise.withResolvers<unknown>();
		const b = bind({ probe: () => answer.promise });
		const first = b.request(3);
		expect(b.probeCalls).toHaveLength(1); // the probe started synchronously inside the hook
		await b.request(4); // the side turn's own context hook, or an overlapping request: returns at once
		await b.request(4);
		expect(b.probeCalls).toHaveLength(1);
		answer.resolve(reply(COMPRESS));
		await first;
		expect(b.compactCalls).toHaveLength(1);
		const probes = b.entries.filter(([t]) => t === "selfcompact-probe");
		expect(probes).toHaveLength(1);
		expect(probes[0][1]).toMatchObject({ request: 1 });
	});
	test("while a fire is settling no probe runs; after it settles the window is closed by the cap", async () => {
		const gate = Promise.withResolvers<void>();
		const done = Promise.withResolvers<void>();
		const b = bind({
			compact: async options => {
				await gate.promise;
				options.onComplete?.({ summary: "s", firstKeptEntryId: "e9", tokensBefore: 60_000 });
				done.resolve();
			},
		});
		await b.request(3);
		expect(b.compactCalls).toHaveLength(1);
		await requests(b, 3, 4);
		expect(b.probeCalls).toHaveLength(1);
		gate.resolve();
		await done.promise;
		await requests(b, 3, 4);
		expect(b.probeCalls).toHaveLength(1);
		expect(b.entries.find(([t]) => t === "selfcompact-fire")?.[1]).toMatchObject({ ok: true });
	});
	// The host commits the fire's compaction and awaits its session_compact emit before calling
	// onComplete (session-maintenance.ts: #commitCompactionEntry, then options.onComplete); the
	// window must stay closed whichever of the two a host reports first.
	for (const sessionCompactFirst of [true, false]) {
		test(`a fire's own compaction does not reopen the window it closed (session_compact ${sessionCompactFirst ? "before" : "after"} onComplete); a later compaction does`, async () => {
			const b: Binding = bind({
				compact: async options => {
					if (sessionCompactFirst) await b.compacted();
					options.onComplete?.({ summary: "s", firstKeptEntryId: "e9", tokensBefore: 60_000 });
					if (!sessionCompactFirst) await b.compacted();
				},
			});
			await b.request(3); // probe 1: compress → fire → the fire's own compaction
			await requests(b, 4, 4);
			expect(b.probeCalls).toHaveLength(1);
			await b.compacted(); // a compaction the extension did not fire
			await b.request(4);
			expect(b.probeCalls).toHaveLength(2);
		});
		test(`a failed fire whose promise has not settled holds off the next probe, so the window still gets one self-summary (session_compact ${sessionCompactFirst ? "before" : "after"} onComplete)`, async () => {
			const firstSettles = Promise.withResolvers<void>();
			const secondCompletes = Promise.withResolvers<void>();
			let calls = 0;
			const b: Binding = bind({
				compact: async options => {
					if (++calls === 1) {
						options.onError?.(new Error("Nothing to compact (session too small)"));
						await firstSettles.promise; // the TUI's executeCompaction flushes queued prompts before it resolves
						return;
					}
					await secondCompletes.promise;
					if (sessionCompactFirst) await b.compacted();
					options.onComplete?.({ summary: "s", firstKeptEntryId: "e9", tokensBefore: 60_000 });
					if (!sessionCompactFirst) await b.compacted();
				},
			});
			await b.request(3); // probe 1 → fire 1 → onError; its promise stays pending
			await requests(b, 2, 4); // a probe here would start a second fire while the first is pending
			firstSettles.resolve();
			await drain();
			secondCompletes.resolve();
			await drain();
			await requests(b, 7, 6); // no compaction the extension did not cause
			await drain();
			expect(b.entries.filter(([t]) => t === "selfcompact-fire").map(([, d]) => d.ok)).toEqual([false, true]);
		});
	}
});

/** A side turn that settles only when its signal aborts: with `answer`, a verdict arriving after the abort; without it, the host's abort error. */
const hangs = (answer?: string) => (options: unknown) => {
	const { signal } = options as { signal?: AbortSignal };
	const { promise, resolve, reject } = Promise.withResolvers<unknown>();
	signal?.addEventListener("abort", () => (answer ? resolve(reply(answer)) : reject(new Error("This operation was aborted"))));
	return promise;
};

/** Sends one request whose probe hangs, lets 20 s pass on fake timers, and reports whether the hook returned. Bounded: a probe with no deadline, or a later one, leaves the hook pending and the answer false. */
async function requestPast20s(b: Binding, turns: number) {
	vi.useFakeTimers();
	let returned = false;
	void Promise.resolve(b.request(turns)).then(() => {
		returned = true;
	});
	vi.advanceTimersByTime(20_000);
	vi.useRealTimers();
	await drain();
	return returned;
}

describe("probe deadline", () => {
	afterEach(() => {
		vi.useRealTimers();
	});
	test("a probe that hangs is cut off by its own deadline, inside the host's 30 s handler budget: recorded as a timeout, no warning, no fire", async () => {
		const b = bind({ probe: hangs() });
		expect(await requestPast20s(b, 3)).toBe(true);
		expect(b.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ verdict: "continue", error: "probe deadline" });
		expect(b.compactCalls).toHaveLength(0);
		expect(b.warnings).toEqual([]);
	});
	test("a compress verdict that lands after the deadline does not fire, and its cost is still recorded", async () => {
		const b = bind({ probe: hangs(COMPRESS) });
		expect(await requestPast20s(b, 3)).toBe(true);
		await drain();
		expect(b.compactCalls).toHaveLength(0);
		expect(b.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ verdict: "continue", error: "probe deadline", cost: 0.0189 });
	});
	test("three failed probes close the window until a compaction the extension did not cause", async () => {
		const b = bind({
			probe: async () => {
				throw new Error("boom");
			},
		});
		await requests(b, 9, 3); // probes at requests 1, 3 and 5; none after the third failure
		expect(b.probeCalls).toHaveLength(3);
		await b.compacted();
		await b.request(12);
		expect(b.probeCalls).toHaveLength(4);
	});
});

describe("failure containment", () => {
	test("a throwing probe or usage lookup is recorded and the hook still returns, with no compaction", async () => {
		const probeThrows = bind({
			probe: async () => {
				throw new Error("boom");
			},
		});
		await probeThrows.request(3);
		expect(probeThrows.compactCalls).toHaveLength(0);
		expect(probeThrows.entries.find(([t]) => t === "selfcompact-probe")?.[1]).toMatchObject({ verdict: "continue", error: "boom" });
		await requests(probeThrows, 2, 4); // once the period gate reopens, the failed probe does not block the next one
		expect(probeThrows.probeCalls).toHaveLength(2);
		const usageThrows = bind({ usageThrows: true });
		await usageThrows.request(3);
		expect(usageThrows.probeCalls).toHaveLength(0);
		// Not a probe: no side turn ran, so it must not be recorded as one.
		expect(usageThrows.entries).toEqual([["selfcompact-error", expect.objectContaining({ v: 1, request: 1, error: "usage unavailable" })]]);
	});
	test("a compaction that rejects after onError is recorded once as a failed fire, leaves the cap unconsumed, and two failures close the window", async () => {
		const b = bind({
			compact: async options => {
				const err = new Error("Nothing to compact (session too small)");
				options.onError?.(err);
				throw err;
			},
		});
		await b.request(3);
		await drain();
		expect(b.entries.filter(([t]) => t === "selfcompact-fire").map(([, d]) => d)).toEqual([
			expect.objectContaining({ ok: false, error: "Nothing to compact (session too small)" }),
		]);
		await requests(b, 2, 4); // the period gate opens and the cap is unconsumed: fire again, fail again
		await drain();
		expect(b.compactCalls).toHaveLength(2);
		await requests(b, 4, 6); // two failures in this window: no more probes
		expect(b.probeCalls).toHaveLength(2);
	});
	test("a compaction that rejects before onError ('already in progress') is still recorded as a failed fire", async () => {
		const b = bind({
			compact: async () => {
				throw new Error("Compaction already in progress");
			},
		});
		await b.request(3);
		await drain();
		expect(b.entries.find(([t]) => t === "selfcompact-fire")?.[1]).toMatchObject({ ok: false, error: "Compaction already in progress" });
		await requests(b, 2, 4);
		expect(b.probeCalls).toHaveLength(2); // the phase returned to idle and the period gate reopened
	});
	test("a compaction that resolves without calling either callback (the TUI swallows 'already in progress') is recorded once as a failed fire and releases the probe", async () => {
		const b = bind({ compact: async () => {} });
		await b.request(3);
		await drain();
		expect(b.entries.filter(([t]) => t === "selfcompact-fire").map(([, d]) => d)).toEqual([
			expect.objectContaining({ ok: false, error: "compaction settled without onComplete or onError" }),
		]);
		await requests(b, 2, 4);
		expect(b.probeCalls).toHaveLength(2); // the phase returned to idle and the period gate reopened
	});
});
