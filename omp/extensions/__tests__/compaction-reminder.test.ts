import { describe, expect, test } from "bun:test";
import extension from "../compaction-reminder";

type Pi = Parameters<typeof extension>[0];
type Sent = { message: { customType: string; content: string; display: boolean }; options: { deliverAs?: string } };
type Entry = Record<string, unknown> & { type: string };

const ASSIGNMENT = `# Target\n- \`omp/config.yml\` only\n\nNon-goals: the fork.\n\n${"Restate every constraint. ".repeat(80)}\n\nReport the commit sha.`;
const SHAKEN = "[shaken ~600 tokens — recover: artifact://7 (region 1)]";

const user = (text: string): Entry => ({ type: "message", message: { role: "user", content: [{ type: "text", text }] } });
const assistant = (text: string): Entry => ({ type: "message", message: { role: "assistant", content: [{ type: "text", text }] } });
const sessionInit = (task: string): Entry => ({ type: "session_init", systemPrompt: "you are a subagent", task, tools: [] });
const compaction: Entry = { type: "compaction", summary: "paraphrase" };
const sddGoal: Entry = { type: "mode_change", mode: "goal", data: { goal: { objective: "[sdd] ship it", status: "active" } } };
const context = (kind: "main" | "sub", branch: Entry[], idle: boolean) => ({
	agent: { kind, id: kind === "sub" ? "0-Worker" : "Main", name: kind === "sub" ? "task" : "main", depth: 0 },
	sessionManager: { getBranch: () => branch },
	isIdle: () => idle,
});

/** One session binding of the extension, driven through a fake `pi`. */
function bind() {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => void>();
	const sent: Sent[] = [];
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => void) => handlers.set(event, handler),
		sendMessage: (message: Sent["message"], options: Sent["options"]) => sent.push({ message, options }),
	} as unknown as Pi;
	extension(pi);
	const emit = (event: string, payload: unknown, ctx?: unknown) => handlers.get(event)?.(payload, ctx);
	return {
		sent,
		turnEnd: (toolResults: number) => emit("turn_end", { toolResults: Array(toolResults).fill({}) }),
		compact: (kind: "main" | "sub", branch: Entry[], idle: boolean) =>
			emit("session_compact", {}, context(kind, branch, idle)),
		shakeEnd: (kind: "main" | "sub", branch: Entry[], idle: boolean, outcome: Record<string, unknown>) =>
			emit(
				"auto_compaction_end",
				{ action: "shake", result: undefined, aborted: false, willRetry: false, ...outcome },
				context(kind, branch, idle)
			),
	};
}

const subagentLead = async () =>
	(await Bun.file(new URL("../compaction-reminder-subagent.md", import.meta.url)).text()).trim();

/**
 * Shake's top-level XML grammar, from `packages/agent/src/compaction/shake.ts`
 * (`OPENING_XML`, `CLOSING_XML`, `scanTextForBlockRanges`) at omp
 * 18.4.3-sami.20260929-152920. A matching tag on its own line opens a span, and a
 * span of 400+ tokens in a custom message is one elidable region — which is why the
 * restated assignment must not sit inside one. Models tags only: a fenced block
 * inside the assignment is still a region of its own. Re-prove against the fork with
 * `collectShakeRegions` when omp moves.
 */
function xmlSpansOf(text: string): Array<{ start: number; end: number }> {
	const opening = /^<([a-z_-]+)(?:\s+[^>]*)?>$/;
	const closing = /^<\/([a-z_-]+)>$/;
	const spans: Array<{ start: number; end: number }> = [];
	const stack: string[] = [];
	let start = -1;
	text.split("\n").forEach((line, index) => {
		const open = opening.exec(line);
		if (open) {
			if (stack.length === 0) start = index;
			stack.push(open[1]);
			return;
		}
		const close = closing.exec(line);
		if (close && stack.length > 0 && stack[stack.length - 1] === close[1]) {
			stack.pop();
			if (stack.length === 0) spans.push({ start, end: index });
		}
	});
	return spans;
}

describe("compaction-reminder", () => {
	test("a subagent's first compaction restates its assignment after the reminder text", async () => {
		const session = bind();
		session.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), assistant("working"), compaction], true);

		expect(session.sent).toHaveLength(1);
		const [{ message }] = session.sent;
		expect(message.customType).toBe("post-compaction-subagent-reminder");
		expect(message.display).toBe(true);
		expect(message.content.startsWith(await subagentLead())).toBe(true);
		expect(message.content).toContain(ASSIGNMENT);
	});

	test("the assignment is the branch's latest session_init task, not a user message", () => {
		const session = bind();
		// A `/tan` clone of a subagent: the fork carries the parent's transcript and
		// session_init, then the clone appends its own and prompts with its own work.
		session.compact(
			"sub",
			[
				sessionInit("the parent's assignment"),
				user("the parent's first prompt"),
				assistant("parent working"),
				sessionInit("the clone's own work"),
				user("the clone's own work"),
				compaction,
			],
			true
		);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toContain("the clone's own work");
		expect(session.sent[0].message.content).not.toContain("the parent's assignment");
		expect(session.sent[0].message.content).not.toContain("the parent's first prompt");
	});

	test("a shaken user message does not reach the restatement", () => {
		const session = bind();
		session.compact("sub", [sessionInit(ASSIGNMENT), user(SHAKEN), compaction], true);

		expect(session.sent[0].message.content).toContain(ASSIGNMENT);
		expect(session.sent[0].message.content).not.toContain(SHAKEN);
	});

	test("a subagent whose branch has no session_init gets the reminder text alone", async () => {
		const session = bind();
		session.compact("sub", [user(ASSIGNMENT), assistant("working"), compaction], true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toBe(await subagentLead());
	});

	test("no span shake would elide encloses the restated assignment", async () => {
		const session = bind();
		session.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);
		const { content } = session.sent[0].message;

		const assignmentLine = content.split("\n").indexOf(ASSIGNMENT.split("\n")[0]);
		expect(assignmentLine).toBeGreaterThan(0);
		const lead = await subagentLead();
		expect(xmlSpansOf(content).filter(span => span.end >= assignmentLine)).toEqual([]);
		// The lead itself is one span, and stays far under shake's 400-token floor.
		expect(xmlSpansOf(lead)).toHaveLength(1);
	});

	test("a subagent's later compactions send nothing", () => {
		const session = bind();
		const branch = [sessionInit(ASSIGNMENT), compaction];
		session.compact("sub", branch, true);
		session.compact("sub", [...branch, compaction], true);

		expect(session.sent).toHaveLength(1);
	});

	test("each subagent binding gets its own first-compaction reminder", () => {
		const first = bind();
		const second = bind();
		first.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);
		second.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);

		expect(first.sent).toHaveLength(1);
		expect(second.sent).toHaveLength(1);
	});

	test("a shake that dropped content is a subagent's first compaction", () => {
		const session = bind();
		session.turnEnd(1);
		session.shakeEnd("sub", [sessionInit(ASSIGNMENT), user(SHAKEN)], false, { skipped: false });

		expect(session.sent.map(s => [s.message.customType, s.options.deliverAs])).toEqual([
			["post-compaction-subagent-reminder", "aside"],
		]);
		expect(session.sent[0].message.content).toContain(ASSIGNMENT);
		session.compact("sub", [sessionInit(ASSIGNMENT), user(SHAKEN), compaction], false);
		expect(session.sent).toHaveLength(1);
	});

	test("a shake that drops nothing, falls through or aborts leaves the reminder to the compaction that follows", () => {
		const session = bind();
		const branch = [sessionInit(ASSIGNMENT)];
		const fellThrough = "Auto-shake reclaimed ~9000 tokens but context is still above the threshold; trying the next preferred compaction method.";
		session.shakeEnd("sub", branch, true, { skipped: true });
		session.shakeEnd("sub", branch, true, { skipped: false, errorMessage: fellThrough });
		session.shakeEnd("sub", branch, true, { aborted: true });
		expect(session.sent).toHaveLength(0);

		session.compact("sub", [...branch, compaction], true);
		expect(session.sent).toHaveLength(1);
	});

	test("a subagent compacting mid-run gets the reminder as an aside", () => {
		const session = bind();
		session.turnEnd(2);
		session.compact("sub", [sessionInit(ASSIGNMENT), compaction], false);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].options.deliverAs).toBe("aside");
	});

	test("a subagent compacting while idle, or at the end of its run, gets the reminder on the next turn", () => {
		const idle = bind();
		idle.turnEnd(2);
		idle.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);
		const runEnd = bind();
		runEnd.turnEnd(0);
		runEnd.compact("sub", [sessionInit(ASSIGNMENT), compaction], false);

		expect(idle.sent.map(s => s.options.deliverAs)).toEqual(["nextTurn"]);
		expect(runEnd.sent.map(s => s.options.deliverAs)).toEqual(["nextTurn"]);
	});

	test("a top-level session gets no subagent reminder, and its shake sends nothing", () => {
		const session = bind();
		session.compact("main", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), compaction], true);
		const paused: Entry = { type: "mode_change", mode: "goal_paused" };
		session.compact("main", [sddGoal, paused, compaction], true);
		session.shakeEnd("main", [sddGoal], true, { skipped: false });

		expect(session.sent).toHaveLength(0);
	});

	test("a top-level session with an active [sdd] goal gets the sdd reminder on every compaction", async () => {
		const sddText = (await Bun.file(new URL("../compaction-reminder.md", import.meta.url)).text()).trim();
		const session = bind();
		const branch = [user(ASSIGNMENT), sddGoal, compaction];
		session.compact("main", branch, true);
		session.turnEnd(1);
		session.compact("main", [...branch, compaction], false);

		expect(session.sent.map(s => [s.message.customType, s.message.content, s.options.deliverAs])).toEqual([
			["post-compaction-reminder", sddText, "nextTurn"],
			["post-compaction-reminder", sddText, "aside"],
		]);
	});
});
