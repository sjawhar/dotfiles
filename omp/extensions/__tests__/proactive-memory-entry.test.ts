// Entry-level tests for proactive-memory.ts, in the shape askgate.test.ts:815-932 uses: mock the
// fork's @oh-my-pi/pi-ai module, import the REAL entry file (not a fake), drive it through one
// "context" step, and inspect what it wrote. These tests exist to pin the entry's own contract —
// how a model reply's stopReason maps to Completion.error, and how content blocks become
// text/toolCalls — independent of the core's own (already-covered) step logic.
import { afterEach, describe, expect, mock, test } from "bun:test";
import type { BranchEntry, Msg, StepRecord } from "../experiments/proactive-memory-core";
import { ID_ALPHABET, ID_LENGTH, STEP_ENTRY_TYPE } from "../experiments/proactive-memory-core";

type Attempt = {
	content: Array<{ type: string; text?: string; id?: string; name?: string; arguments?: Record<string, unknown> }>;
	usage: { input: number; output: number; cacheRead: number; cacheWrite: number };
	stopReason: string;
	errorMessage?: string;
};

// The entry imports @oh-my-pi/pi-ai, which resolves only inside omp; this stub stands in for it.
let attempts: Attempt[] = [];
const completions: Array<Record<string, unknown>> = [];
mock.module("@oh-my-pi/pi-ai", () => ({
	completeSimple: async (_model: unknown, _context: unknown, options: Record<string, unknown> & { onAttempt?: (m: unknown) => void }) => {
		completions.push(options);
		for (const attempt of attempts) options.onAttempt?.(attempt as never);
		return attempts.at(-1);
	},
	retryTransientCompletion: (run: () => Promise<unknown>) => run(),
}));

const MODEL = { provider: "anthropic", id: "claude-fable-5-1" };
// T9: a real tool-calling Phase 1 reply ends with stopReason "toolUse" (packages/ai/src/types.ts:928
// @ec43b526), never "stop" — a plain text-only reply is what actually returns "stop". Building
// every attempt with "stop" regardless of whether it carries tool calls meant a regression that
// checked `stopReason === "stop"` specifically (treating "toolUse" as an error) could never fail
// here, since the fixture never produced "toolUse" at all.
const clean = (text: string, toolCalls: Array<{ name: string; arguments: Record<string, unknown> }> = []): Attempt => ({
	content: [{ type: "text", text }, ...toolCalls.map((c, i) => ({ type: "toolCall", id: `tc${i}`, name: c.name, arguments: c.arguments }))],
	usage: { input: 5, output: 7, cacheRead: 0, cacheWrite: 0 },
	stopReason: toolCalls.length > 0 ? "toolUse" : "stop",
});

type Handler = (event: { messages: Msg[] }, ctx: unknown) => Promise<{ messages: Msg[] } | undefined>;

/** Binds the real entry to a fake `pi`/`ctx` and runs one "context" step through it, returning
 * the StepRecord entries the core appended (via appendEntry(STEP_ENTRY_TYPE, ...)). */
async function stepOnce(messages: Msg[]) {
	const { default: proactiveMemory } = await import("../experiments/proactive-memory");
	const handlers = new Map<string, Handler>();
	const steps: StepRecord[] = [];
	const fakePi = {
		on: (event: string, handler: Handler) => handlers.set(event, handler),
		appendEntry: (type: string, data: unknown) => {
			if (type === STEP_ENTRY_TYPE) steps.push(data as StepRecord);
		},
		logger: { debug: () => {}, warn: () => {} },
		typebox: { Type: { Object: (shape: unknown) => shape, String: (opts: unknown) => opts } },
	};
	proactiveMemory(fakePi as never);
	const branch: BranchEntry[] = [];
	const ctx = {
		agent: { kind: "main" },
		sessionManager: { getBranch: () => branch, getSessionId: () => "sess-e" },
		models: { resolve: () => MODEL },
		modelRegistry: { resolver: () => "resolved-key" },
	};
	const result = await handlers.get("context")?.({ messages }, ctx);
	return { result, steps };
}

const PROMPT: Msg[] = [{ role: "user", content: "Do the thing.", timestamp: 1 } as Msg];

describe("proactive-memory.ts, the entry", () => {
	// R2-4: the entry reads the REAL process.env at import time (createProactiveMemory({ env:
	// process.env, ... })); without saving and restoring it, a leaked LEGION_TREE/LEGION_ROLE/
	// LEGION_CONTROLLER from whatever environment happens to run this suite (a Legion pane, a
	// CI box someone set one on) would silently skip every test here with no failure at all —
	// the Legion check running inside createProactiveMemory would return before any of these
	// assertions are ever reached, and stepOnce's own steps array would just stay empty.
	const saved = { ...process.env };
	afterEach(() => {
		completions.length = 0;
		attempts = [clean("looks fine")];
		for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
		Object.assign(process.env, saved);
	});
	for (const key of ["LEGION_TREE", "LEGION_ROLE", "LEGION_CONTROLLER"]) delete process.env[key];

	attempts = [clean("looks fine")];

	test("a Phase 1 reply cut off at the token limit (stopReason length) reports error \"length\", not applied tool calls", async () => {
		attempts = [clean("(truncated", [{ name: "memory_save_knowledge", arguments: { content: "partial" } }])];
		attempts[0].stopReason = "length";
		const { steps } = await stepOnce(PROMPT);
		expect(steps).toHaveLength(1);
		expect(steps[0].phase1?.error).toBe("length");
		// applyOperations must have received no calls: nothing recorded as applied.
		expect(steps[0].phase1?.operations).toEqual([]);
	});

	test("a Phase 1 reply that errors reports the error message", async () => {
		attempts = [{ content: [], usage: { input: 1, output: 0, cacheRead: 0, cacheWrite: 0 }, stopReason: "error", errorMessage: "rate limited" }];
		const { steps } = await stepOnce(PROMPT);
		expect(steps[0].phase1?.error).toBe("rate limited");
	});

	test("a Phase 1 reply that errors with no message falls back to \"error\"", async () => {
		attempts = [{ content: [], usage: { input: 1, output: 0, cacheRead: 0, cacheWrite: 0 }, stopReason: "error" }];
		const { steps } = await stepOnce(PROMPT);
		expect(steps[0].phase1?.error).toBe("error");
	});

	test("an aborted Phase 1 reply reports error \"aborted\"", async () => {
		attempts = [{ content: [], usage: { input: 1, output: 0, cacheRead: 0, cacheWrite: 0 }, stopReason: "aborted" }];
		const { steps } = await stepOnce(PROMPT);
		expect(steps[0].phase1?.error).toBe("aborted");
	});

	test("a leaked LEGION_ROLE through the REAL process.env makes no completion call, records skipped: legion (the isolation this file relies on, proven rather than assumed)", async () => {
		process.env.LEGION_ROLE = "tester";
		const { steps } = await stepOnce(PROMPT);
		expect(completions).toHaveLength(0);
		expect(steps).toHaveLength(1);
		expect(steps[0].skipped).toBe("legion");
	});

	test("a clean completion has no error and its text/tool calls come from the real content blocks", async () => {
		attempts = [clean("noted", [{ name: "memory_save_knowledge", arguments: { content: "the answer is 42" } }])];
		const { steps } = await stepOnce(PROMPT);
		expect(steps[0].phase1?.error).toBeUndefined();
		// The save_knowledge call was read from the toolCall content block and applied.
		expect(steps[0].phase1?.operations).toMatchObject([{ action: "save_knowledge", ok: true, content: "the answer is 42" }]);
	});
});

describe("proactive-memory.ts, newId", () => {
	test("draws ids from the fork-safe alphabet at the expected length", async () => {
		attempts = [clean("looks fine")];
		const { steps } = await stepOnce([{ role: "user", content: "Please remember: the sky is blue today.", timestamp: 1 } as Msg]);
		// Force a save so newId is exercised: rerun with a tool call requesting a save.
		void steps;
		attempts = [clean("noted", [{ name: "memory_save_knowledge", arguments: { content: "the sky is blue" } }])];
		const { steps: steps2 } = await stepOnce([{ role: "user", content: "Remember this too.", timestamp: 2 } as Msg]);
		const op = steps2[0].phase1?.operations.find(o => o.action === "save_knowledge");
		expect(op?.ok).toBe(true);
		expect(op?.id).toHaveLength(ID_LENGTH);
		expect(op?.id).toMatch(new RegExp(`^[${ID_ALPHABET}]+$`));
	});
});
