import { afterEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { createDecisionReviewGate, type GateDeps, LEAD_SESSION_ID, METHOD_SESSIONS, METHOD_TOPICS, METHOD_WORDS } from "../decision-review-gate";

type Result = { block?: boolean; reason?: string } | undefined;
type Handler = (event: unknown, ctx: unknown) => Promise<Result>;
type Line = Record<string, unknown>;

const LEAD_FILE = `/s/-src-x/2026-10-21T17-57-29-198Z_${LEAD_SESSION_ID}.jsonl`;
const SUB_ID = "01a17a95-ee79-7214-8df2-51394559c2f5";
const SUB_FILE = `/s/-src-x/2026-10-21T17-57-29-198Z_${LEAD_SESSION_ID}/Helper.jsonl`;
const OTHER_ID = "01a17998-6996-746c-9585-6e829280c54e";
const OTHER_FILE = `/s/-src-y/2026-10-26T19-30-00-000Z_${OTHER_ID}.jsonl`;
const SLICE = METHOD_SESSIONS[0];
const DECISION = "Owners write Lean in their own session from now on.";
const HOUR = 60 * 60 * 1000;

const dirs: string[] = [];
afterEach(() => {
	for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

let n = 0;

/** The gate bound to a fake `pi`, its review directory, and what it logged. */
function bind(overrides: Partial<GateDeps> = {}) {
	const root = mkdtempSync(path.join(os.tmpdir(), "decision-review-gate-"));
	dirs.push(root);
	const reviews = path.join(root, "reviews");
	mkdirSync(reviews);
	const lines: Line[] = [];
	const debug: unknown[] = [];
	let handler: Handler | undefined;
	const pi = {
		on: (event: string, h: Handler) => {
			if (event === "tool_call") handler = h;
		},
		logger: { debug: (...args: unknown[]) => void debug.push(args) },
	};
	createDecisionReviewGate({ reviewDir: reviews, append: line => void lines.push(JSON.parse(line)), ...overrides })(pi as never);
	/** `file: null` is a session with no transcript (--no-session). */
	const ctx = (id = LEAD_SESSION_ID, file: string | null = LEAD_FILE) => ({ cwd: "/work", sessionManager: { getSessionId: () => id, getSessionFile: () => file ?? undefined } });
	const fire = (toolName: string, input: Record<string, unknown>, c: unknown = ctx(), toolCallId = `t${++n}`) => {
		if (!handler) throw new Error("no tool_call handler registered");
		return handler({ type: "tool_call", toolName, toolCallId, input }, c);
	};
	const device = (name: string, args: Record<string, unknown>, c?: unknown, toolCallId?: string) =>
		fire("write", { path: `xd://${name}`, content: JSON.stringify(args) }, c, toolCallId);
	const send = (message: string, sessionId: string = SLICE, c?: unknown) => device("envoy_send", { session_id: sessionId, message }, c);
	const publish = (message: string, topic: string = METHOD_TOPICS[0], c?: unknown) => device("envoy_publish", { topic, message }, c);
	/** A review file in the review directory, `ageMs` old. */
	const review = (name: string, text: string, ageMs = 0) => {
		const file = path.join(reviews, name);
		writeFileSync(file, text);
		const when = new Date(Date.now() - ageMs);
		utimesSync(file, when, when);
		return file;
	};
	return { root, reviews, lines, debug, ctx, fire, device, send, publish, review };
}

describe("calls the gate leaves alone", () => {
	test("a write that is not an Envoy send or publish runs and is not logged", async () => {
		const g = bind();
		expect(await g.fire("write", { path: "/work/plan.md", content: DECISION })).toBeUndefined();
		expect(await g.device("envoy_whoami", { message: DECISION })).toBeUndefined();
		expect(await g.fire("bash", { command: `echo "${DECISION}"` })).toBeUndefined();
		expect(g.lines).toEqual([]);
	});

	test("a method decision to a session outside the stream's slices runs, and is logged as a pass", async () => {
		const g = bind();
		expect(await g.send(DECISION, OTHER_ID)).toBeUndefined();
		expect(await g.publish(DECISION, "notifications.team.someone-else")).toBeUndefined();
		expect(g.lines.map(l => [l.outcome, l.why, l.target])).toEqual([
			["pass", "not a method-decision target", OTHER_ID],
			["pass", "not a method-decision target", "notifications.team.someone-else"],
		]);
	});

	test("a message to a slice with no method words runs, and is logged as a pass", async () => {
		const g = bind();
		expect(await g.publish("Status: the build is green on main.", "notifications.team.linux-owners")).toBeUndefined();
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "pass", why: "no method words", tool: "envoy_publish", methodWords: [] });
	});

	test("another session's method decision to a slice runs untouched: no check, no log line", async () => {
		const g = bind();
		expect(await g.send(DECISION, SLICE, g.ctx(OTHER_ID, OTHER_FILE))).toBeUndefined();
		expect(await g.send(DECISION, SLICE, g.ctx(OTHER_ID, null))).toBeUndefined();
		expect(g.lines).toEqual([]);
	});
});

describe("a method decision to a slice", () => {
	test("every method word, in any case, makes a message to every target a method decision", async () => {
		const g = bind();
		const targets: Array<[string, string]> = [
			...METHOD_TOPICS.map((topic): [string, string] => ["envoy_publish", topic]),
			...METHOD_SESSIONS.map((id): [string, string] => ["envoy_send", id]),
			...METHOD_SESSIONS.map((id): [string, string] => ["envoy_publish", `notifications.agent.${id}`]),
		];
		for (const word of METHOD_WORDS) {
			for (const [tool, target] of targets) {
				const args = tool === "envoy_send" ? { session_id: target, message: `About the ${word.toUpperCase()} step.` } : { topic: target, message: `About the ${word.toUpperCase()} step.` };
				const result = await g.device(tool, args);
				expect({ word, target, block: result?.block }).toEqual({ word, target, block: true });
			}
		}
		expect(g.lines.every(l => l.outcome === "refuse")).toBe(true);
	});

	test("without a review line it is refused: the reason names what is missing and how to get it", async () => {
		const g = bind();
		const result = await g.send(DECISION);
		expect(result?.block).toBe(true);
		expect(result?.reason).toContain("carries no `review: <path>` line");
		expect(result?.reason).toContain(
			`dispatch a fresh reviewer (task agent reviewer or scout) with the decision text, INFRA-109 (dispatch doc-read --issue INFRA-109) and dispatch://LINUX-31/artifact/sami-decisions-md; have it write its verdict to ${g.reviews}/<name>.md ending with VERDICT: PASS or VERDICT: FAIL; then add review: <path> to the message`,
		);
		expect(result?.reason).toContain('"owners write"');
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "refuse", tool: "envoy_send", target: SLICE, session: LEAD_SESSION_ID, methodWords: ["owners write"] });
	});

	test("a review path that does not exist is refused", async () => {
		const g = bind();
		const missing = path.join(g.reviews, "nothing-here.md");
		const result = await g.send(`${DECISION}\nreview: ${missing}`);
		expect(result?.block).toBe(true);
		expect(result?.reason).toContain(`review: ${missing} does not exist`);
		expect(g.lines[0]).toMatchObject({ outcome: "refuse", review: [missing] });
	});

	test("a review older than 3 hours is refused; one just inside 3 hours passes", async () => {
		const g = bind();
		const old = g.review("old.md", "Looks right.\nVERDICT: PASS\n", 3 * HOUR + 60_000);
		const stale = await g.send(`${DECISION}\nreview: ${old}`);
		expect(stale?.block).toBe(true);
		expect(stale?.reason).toContain(`review: ${old} was last modified 3h01m ago`);
		const recent = g.review("recent.md", "Looks right.\nVERDICT: PASS\n", 3 * HOUR - 60_000);
		expect(await g.send(`${DECISION}\nreview: ${recent}`)).toBeUndefined();
	});

	test("a review whose verdict is FAIL is refused, also when an earlier line said PASS", async () => {
		const g = bind();
		const fail = g.review("fail.md", "Misreads section 3.4.\nVERDICT: FAIL\n");
		const failed = await g.send(`${DECISION}\nreview: ${fail}`);
		expect(failed?.block).toBe(true);
		expect(failed?.reason).toContain(`review: ${fail} does not end on VERDICT: PASS (its last verdict line is "VERDICT: FAIL")`);
		const reversed = g.review("reversed.md", "VERDICT: PASS\nSecond read: misreads item 7.\nVERDICT: FAIL\n");
		expect((await g.send(`${DECISION}\nreview: ${reversed}`))?.block).toBe(true);
		const none = g.review("none.md", "I think it is fine.\n");
		const unjudged = await g.send(`${DECISION}\nreview: ${none}`);
		expect(unjudged?.reason).toContain(`review: ${none} has no line starting VERDICT:`);
	});

	test("a PASS review outside the review directory is refused, through .. or a symlink alike", async () => {
		const g = bind();
		const outside = path.join(g.root, "elsewhere.md");
		writeFileSync(outside, "VERDICT: PASS\n");
		const viaDots = `${g.reviews}/../elsewhere.md`;
		const dots = await g.send(`${DECISION}\nreview: ${viaDots}`);
		expect(dots?.block).toBe(true);
		expect(dots?.reason).toContain(`review: ${viaDots} is not a file under ${g.reviews}/`);
		const relative = await g.send(`${DECISION}\nreview: reviews/elsewhere.md`);
		expect(relative?.reason).toContain("review: reviews/elsewhere.md is not a file under");
		const link = path.join(g.reviews, "link.md");
		symlinkSync(outside, link);
		const linked = await g.send(`${DECISION}\nreview: ${link}`);
		expect(linked?.reason).toContain(`review: ${link} is not a file under ${g.reviews}/`);
		expect(g.lines.map(l => l.outcome)).toEqual(["refuse", "refuse", "refuse"]);
	});

	test("a fresh PASS review in the review directory lets it through, and the pass is logged with the review", async () => {
		const g = bind();
		const ok = g.review("owners-write-lean.md", "Matches INFRA-109 section 3.4 item 7.\nVERDICT: PASS — consistent with the design\n");
		expect(await g.send(`${DECISION}\n\nreview: \`${ok}\`.`)).toBeUndefined();
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "pass", why: "review PASS", review: [ok], methodWords: ["owners write"] });
	});

	test("a lead subagent's method decision is held to the same rule", async () => {
		const g = bind();
		const result = await g.send(DECISION, SLICE, g.ctx(SUB_ID, SUB_FILE));
		expect(result?.block).toBe(true);
		expect(g.lines[0]).toMatchObject({ outcome: "refuse", session: SUB_ID, rootSession: LEAD_SESSION_ID });
		expect((await g.send(DECISION, SLICE, g.ctx(LEAD_SESSION_ID, null)))?.block).toBe(true);
	});

	test("a direct envoy tool call is checked; the nested dispatch of a write that passed is not checked twice", async () => {
		const g = bind();
		const direct = await g.fire("envoy_publish", { topic: "notifications.role.linux-baseline", message: "Use the planner skill." });
		expect(direct?.block).toBe(true);
		expect(await g.device("envoy_send", { session_id: SLICE, message: "Build is green." }, undefined, "nested-1")).toBeUndefined();
		expect(await g.fire("envoy_send", { session_id: SLICE, message: "Build is green." }, g.ctx(), "nested-1")).toBeUndefined();
		expect(g.lines.map(l => [l.outcome, l.via])).toEqual([
			["refuse", "direct"],
			["pass", "write"],
		]);
	});
});

describe("the escape", () => {
	test("no-method-decision: with a reason lets the message through and logs it as an escape", async () => {
		const g = bind();
		const message = "The architect role restarted at 22:10Z; nothing changes.\nno-method-decision: an outage notice, not a method change";
		expect(await g.send(message)).toBeUndefined();
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "escape", escape: "an outage notice, not a method change", methodWords: ["architect", "method change"] });
	});

	test("no-method-decision: with no reason is no escape", async () => {
		const g = bind();
		expect((await g.send(`${DECISION}\nno-method-decision:`))?.block).toBe(true);
		expect(g.lines[0]).toMatchObject({ outcome: "refuse" });
	});
});

describe("failing open", () => {
	test("an internal error lets the call through and logs it", async () => {
		const g = bind({
			now: () => {
				throw new Error("clock broke");
			},
		});
		expect(await g.send(DECISION)).toBeUndefined();
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "error", error: "clock broke" });
		expect(g.debug).toHaveLength(1);
	});

	test("device content that is not JSON lets the call through and logs it", async () => {
		const g = bind();
		expect(await g.fire("write", { path: "xd://envoy_send", content: "{not json" })).toBeUndefined();
		expect(g.lines[0]).toMatchObject({ outcome: "error" });
	});

	test("a check that outlives its deadline lets the call through and logs it", async () => {
		const hang = () => new Promise<never>(() => {});
		const g = bind({ deadlineMs: 30, fs: { realpath: hang, stat: hang, readFile: hang } });
		const file = path.join(g.reviews, "slow.md");
		const started = Date.now();
		expect(await g.send(`${DECISION}\nreview: ${file}`)).toBeUndefined();
		expect(Date.now() - started).toBeLessThan(1_000);
		expect(g.lines).toEqual([expect.objectContaining({ outcome: "timeout", deadlineMs: 30 })]);
	});

	test("a log write that fails never changes the decision", async () => {
		const g = bind({
			append: () => {
				throw new Error("disk full");
			},
		});
		expect((await g.send(DECISION))?.block).toBe(true);
		expect(await g.send("Build is green.")).toBeUndefined();
	});
});
