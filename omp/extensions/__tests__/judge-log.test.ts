import { describe, expect, test } from "bun:test";
import { type CompleteRequest, type Completion, createJudgeLog, type RatingLine } from "../experiments/judge-log-core";

const SESSION = "/home/u/.omp/agent/sessions/-src-x/2026-09-30T00-00-00-000Z_0000.jsonl";
const RATING = '{"difficulty":4,"spec":2,"axis":"reasoning","risk":"guesses the schema"}';
const NOW = Date.UTC(2026, 9, 1, 20, 0, 0);

function harness(complete: (req: CompleteRequest) => Promise<Completion>, append?: (line: string) => Promise<void>) {
	let handler: (event: unknown, ctx: unknown) => unknown = () => undefined;
	const written: string[] = [];
	const warnings: Array<{ msg: string; meta: { toolCallId: string; itemIndex: number; error: string } }> = [];
	const pi = {
		on: (event: string, h: (event: unknown, ctx: unknown) => unknown) => {
			if (event === "tool_execution_start") handler = h;
		},
		logger: { warn: (msg: string, meta: unknown) => warnings.push({ msg, meta: meta as (typeof warnings)[number]["meta"] }) },
	};
	createJudgeLog({ complete, append: append ?? (async line => void written.push(line)), now: () => NOW })(pi);
	const ctx = {
		models: { resolve: (spec: string): { provider: string; id: string } | undefined => (spec === "@judgeLog" ? { provider: "anthropic", id: "claude-sonnet-5" } : undefined) },
		sessionManager: { getSessionFile: (): string | undefined => SESSION, getSessionId: () => "sid" },
	};
	const start = (toolCallId: string, args: unknown, toolName = "task") => handler({ toolCallId, toolName, args }, ctx);
	// The fakes resolve at once, so every detached rating finishes inside the microtask queue; one macrotask turn
	// (setImmediate) runs only after that queue has drained. No wall-clock wait.
	const settle = () => {
		const { promise, resolve } = Promise.withResolvers<void>();
		setImmediate(resolve);
		return promise;
	};
	const lines = (): RatingLine[] => written.map(line => JSON.parse(line));
	return { ctx, written, warnings, lines, start, settle };
}
const ok = (text: string) => async (): Promise<Completion> => ({ text });

describe("judge-log", () => {
	test("the dispatch never waits on the judge: the handler returns before the rating, which lands after", async () => {
		const answer = Promise.withResolvers<Completion>();
		const h = harness(() => answer.promise);
		expect(h.start("toolu_0", { task: "x" })).toBeUndefined();
		await h.settle();
		expect(h.written).toEqual([]);
		answer.resolve({ text: RATING });
		await h.settle();
		expect(h.lines()).toHaveLength(1);
	});

	test("a flat dispatch is rated blind and logged as one JSON line", async () => {
		const seen: CompleteRequest[] = [];
		const h = harness(async req => {
			seen.push(req);
			return { text: `Here you go: ${RATING}` };
		});
		h.start("toolu_1", { agent: "oracle", name: "Scout", task: "Find every caller of foo() and print $& and $'", effort: "high" });
		await h.settle();
		expect(seen).toHaveLength(1);
		expect(seen[0].model).toEqual({ provider: "anthropic", id: "claude-sonnet-5" });
		expect(seen[0].user).toContain("<assignment>\nFind every caller of foo() and print $& and $'\n</assignment>");
		expect(`${seen[0].system}${seen[0].user}`).not.toContain("oracle");
		expect(`${seen[0].system}${seen[0].user}`).not.toContain("Scout");
		expect(h.written).toHaveLength(1);
		expect(h.written[0].endsWith("}\n")).toBe(true);
		expect(h.lines()).toEqual([
			{
				at: "2026-10-01T20:00:00.000Z",
				sessionFile: SESSION,
				toolCallId: "toolu_1",
				itemIndex: 0,
				assignment: "Find every caller of foo() and print $& and $'",
				difficulty: 4,
				spec: 2,
				axis: "reasoning",
				risk: "guesses the schema",
				judgeModel: "anthropic/claude-sonnet-5",
			},
		]);
	});

	test("a batch dispatch is one line per item with the shared context prefixed", async () => {
		const users: string[] = [];
		const h = harness(async req => {
			users.push(req.user);
			return { text: RATING };
		});
		h.start("toolu_2", { context: "Repo is legion.", tasks: [{ task: "A" }, { task: "B", agent: "deep" }] });
		await h.settle();
		expect(users.some(u => u.includes("<assignment>\nRepo is legion.\n\nA\n</assignment>"))).toBe(true);
		expect(users.some(u => u.includes("<assignment>\nRepo is legion.\n\nB\n</assignment>"))).toBe(true);
		expect(h.lines().map(l => [l.toolCallId, l.itemIndex, l.assignment])).toEqual([
			["toolu_2", 0, "Repo is legion.\n\nA"],
			["toolu_2", 1, "Repo is legion.\n\nB"],
		]);
	});

	test("a long assignment is clipped in the middle for the judge, and its first 500 characters are logged", async () => {
		let user = "";
		const h = harness(async req => {
			user = req.user;
			return { text: RATING };
		});
		const long = Array.from({ length: 3_000 }, (_, i) => `w${i}`).join(" ");
		h.start("toolu_3", { task: long });
		await h.settle();
		expect(user).toContain("characters elided");
		expect(user.length).toBeLessThan(14_000);
		expect(user).toContain(long.slice(0, 100));
		expect(user).toContain(long.slice(-100));
		expect(h.lines()[0].assignment).toBe(long.slice(0, 500));
	});

	test("another tool, or a session with no file, is not rated", async () => {
		let calls = 0;
		const h = harness(async () => {
			calls++;
			return { text: RATING };
		});
		h.start("toolu_4", { command: "ls" }, "bash");
		h.ctx.sessionManager.getSessionFile = () => undefined;
		h.start("toolu_5", { task: "x" });
		await h.settle();
		expect(calls).toBe(0);
		expect(h.written).toEqual([]);
	});

	test.each([
		["no @judgeLog role", null, /role @judgeLog resolves to no model/],
		["a provider error", async (): Promise<Completion> => ({ text: "", error: "overloaded" }), /overloaded/],
		[
			"a thrown completion",
			async (): Promise<Completion> => {
				throw new Error("boom");
			},
			/boom/,
		],
		["a rejected completion", () => Promise.reject(new Error("rejected")), /rejected/],
		["an answer that is not a rating", ok("I'd say about a 4."), /not a rating: "I'd say about a 4\."/],
		["a rating out of range", ok('{"difficulty":7,"spec":2,"axis":"reasoning","risk":"r"}'), /not a rating/],
		["an unknown axis", ok('{"difficulty":3,"spec":2,"axis":"vibes","risk":"r"}'), /not a rating/],
	] as const)("%s is logged as a warning and writes nothing", async (_label, complete, error) => {
		const unhandled: unknown[] = [];
		const listener = (err: unknown) => unhandled.push(err);
		process.on("unhandledRejection", listener);
		try {
			let calls = 0;
			const h = harness(req => {
				calls++;
				return (complete ?? ok(RATING))(req as never);
			});
			if (complete === null) h.ctx.models.resolve = () => undefined;
			expect(h.start("toolu_6", { task: "x" })).toBeUndefined();
			await h.settle();
			expect(h.warnings).toEqual([{ msg: expect.stringContaining("judge_log"), meta: expect.objectContaining({ toolCallId: "toolu_6", itemIndex: 0, error: expect.stringMatching(error) }) }]);
			expect(h.written).toEqual([]);
			if (complete === null) expect(calls).toBe(0);
		} finally {
			process.off("unhandledRejection", listener);
		}
		expect(unhandled).toEqual([]);
	});

	test("a log that cannot be written is logged as a warning", async () => {
		const h = harness(ok(RATING), async () => {
			throw new Error("ENOTDIR: not a directory");
		});
		expect(h.start("toolu_7", { task: "x" })).toBeUndefined();
		await h.settle();
		expect(h.warnings.map(w => w.meta.error)).toEqual(["ENOTDIR: not a directory"]);
	});
});
