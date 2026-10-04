import { describe, expect, test } from "bun:test";
import extension from "../compaction-reminder";

type Pi = Parameters<typeof extension>[0];
type Sent = { message: { customType: string; content: string; display: boolean }; options: { deliverAs?: string } };
type Entry = Record<string, unknown> & { type: string };
type Kind = "main" | "sub";

const PARENT = "Main";
const ASSIGNMENT = `# Target\n- \`omp/config.yml\` only\n\nNon-goals: the fork.\n\n${"Restate every constraint. ".repeat(80)}\n\nReport the commit sha.`;
const SHAKEN = "[shaken ~600 tokens — recover: artifact://7 (region 1)]";

const user = (text: string): Entry => ({ type: "message", message: { role: "user", content: [{ type: "text", text }] } });
const assistant = (text: string): Entry => ({ type: "message", message: { role: "assistant", content: [{ type: "text", text }] } });
const sessionInit = (task: string): Entry => ({ type: "session_init", systemPrompt: "you are a subagent", task, tools: [] });
const compaction: Entry = { type: "compaction", summary: "paraphrase" };
const sddGoal: Entry = { type: "mode_change", mode: "goal", data: { goal: { objective: "[sdd] ship it", status: "active" } } };
/** An IRC message delivered to an idle agent, as the fork persists it. */
const ircIncoming = (from: string, message: string): Entry => ({
	type: "custom_message",
	customType: "irc:incoming",
	content: `<irc>\nIncoming IRC message from agent \`${from}\`:\n\n${message}\n</irc>`,
	display: true,
	details: { id: `${from}-1`, from, message },
});
/** The parent's IRC message to a running subagent: a steer rendered from the fork's parent-irc template. */
const parentSteer = (message: string): Entry => ({
	type: "message",
	message: {
		role: "user",
		content: `[Wait interrupted by message]\n<irc from="parent" agent="${PARENT}">\n${message}\n</irc>`,
		steering: true,
		attribution: "agent",
	},
});
const yieldReminder: Entry = {
	type: "message",
	message: { role: "user", content: "You must call yield.", attribution: "agent", synthetic: true },
};
const context = (kind: Kind, branch: Entry[], idle: boolean, tokens?: number) => ({
	agent: { kind, parentId: kind === "sub" ? PARENT : undefined },
	sessionManager: { getBranch: () => branch },
	isIdle: () => idle,
	getContextUsage: () => (tokens === undefined ? undefined : { tokens, contextWindow: 1_000_000, percent: 0 }),
});

/** One session binding of the extension, driven through a fake `pi`. */
function bind() {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => void>();
	const sent: Sent[] = [];
	const entries: Array<[string, unknown]> = [];
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => void) => handlers.set(event, handler),
		sendMessage: (message: Sent["message"], options: Sent["options"]) => sent.push({ message, options }),
		appendEntry: (customType: string, data: unknown) => entries.push([customType, data]),
	} as unknown as Pi;
	extension(pi);
	const emit = (event: string, payload: unknown, ctx?: unknown) => handlers.get(event)?.(payload, ctx);
	return {
		sent,
		entries,
		turnEnd: (toolResults: number) => emit("turn_end", { toolResults: Array(toolResults).fill({}) }),
		compact: (kind: Kind, branch: Entry[], idle: boolean) => emit("session_compact", {}, context(kind, branch, idle)),
		shakeStart: (kind: Kind, tokens: number) =>
			emit("auto_compaction_start", { reason: "threshold", action: "shake" }, context(kind, [], false, tokens)),
		shakeEnd: (kind: Kind, branch: Entry[], idle: boolean, outcome: Record<string, unknown>, tokens?: number) =>
			emit(
				"auto_compaction_end",
				{ action: "shake", result: undefined, aborted: false, willRetry: false, ...outcome },
				context(kind, branch, idle, tokens)
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
 * restated text must not sit inside one. Models tags only: a fenced block inside the
 * restated text is still a region of its own. Re-prove against the fork with
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
		expect(message.content).not.toContain("LATEST_FOLLOW_UP>");
	});

	test("the assignment is the branch's session_init task, not its first user message", () => {
		const session = bind();
		// A `/tan` clone: the fork carries the parent session's whole transcript, then
		// the clone records its own session_init and prompts with its own work.
		session.compact(
			"sub",
			[
				user("the parent's first prompt"),
				ircIncoming(PARENT, "a message to the parent session"),
				assistant("parent working"),
				sessionInit("the clone's own work"),
				user("the clone's own work"),
				compaction,
			],
			true
		);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toContain("the clone's own work");
		expect(session.sent[0].message.content).not.toContain("the parent's first prompt");
		expect(session.sent[0].message.content).not.toContain("a message to the parent session");
	});

	test("the dispatcher's latest follow-up is restated after the assignment", () => {
		const session = bind();
		session.compact(
			"sub",
			[
				sessionInit(ASSIGNMENT),
				user(ASSIGNMENT),
				assistant("done; yielding"),
				ircIncoming(PARENT, "round 2: fix the first finding"),
				ircIncoming("Peer", "a peer's chatter"),
				yieldReminder,
				ircIncoming(PARENT, "round 3: also fix the second; report both shas"),
				compaction,
			],
			true
		);

		const { content } = session.sent[0].message;
		expect(content).toContain(ASSIGNMENT);
		expect(content).toContain("<LATEST_FOLLOW_UP>\nround 3: also fix the second; report both shas\n</LATEST_FOLLOW_UP>");
		expect(content).not.toContain("round 2");
		expect(content).not.toContain("a peer's chatter");
		expect(content).not.toContain("You must call yield.");
		expect(content.indexOf("<ORIGINAL_ASSIGNMENT>")).toBeLessThan(content.indexOf("<LATEST_FOLLOW_UP>"));
	});

	test("a steer whose <irc> block a shake already swapped out is restated as it stands, not skipped", () => {
		const session = bind();
		const shakenSteer: Entry = {
			type: "message",
			message: { role: "user", content: `[Wait interrupted by message]\n${SHAKEN}`, steering: true, attribution: "agent" },
		};
		session.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), ircIncoming(PARENT, "an older follow-up"), shakenSteer, compaction], true);

		expect(session.sent[0].message.content).toContain(`<LATEST_FOLLOW_UP>\n${SHAKEN}\n</LATEST_FOLLOW_UP>`);
		expect(session.sent[0].message.content).not.toContain("an older follow-up");
	});

	test("a parent's steer to a running subagent is restated by its body", () => {
		const session = bind();
		session.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), parentSteer("stop at the tests"), compaction], true);

		const { content } = session.sent[0].message;
		expect(content).toContain("<LATEST_FOLLOW_UP>\nstop at the tests\n</LATEST_FOLLOW_UP>");
		expect(content).not.toContain("[Wait interrupted by message]");
	});

	test("a steer counts by its flag, whatever its wrapper says", () => {
		const session = bind();
		// The parent-irc template's first line, reworded: the steer is still the latest instruction.
		const reworded: Entry = {
			type: "message",
			message: {
				role: "user",
				content: `[Wait interrupted by a message]\n<irc from="parent" agent="${PARENT}">\nround 3: STOP, output TALLY-11 instead\n</irc>`,
				steering: true,
				attribution: "agent",
			},
		};
		const branch = [sessionInit(ASSIGNMENT), user(ASSIGNMENT), ircIncoming(PARENT, "round 2: output TALLY-10"), reworded, compaction];
		session.compact("sub", branch, true);

		const { content } = session.sent[0].message;
		expect(content).toContain("round 3: STOP, output TALLY-11 instead");
		expect(content).not.toContain("round 2: output TALLY-10");
	});

	test("a person's steer is a follow-up too", () => {
		const session = bind();
		const personSteer: Entry = {
			type: "message",
			message: { role: "user", content: "use the staging bucket", steering: true, attribution: "user" },
		};
		session.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), ircIncoming(PARENT, "round 2"), personSteer, compaction], true);

		expect(session.sent[0].message.content).toContain("<LATEST_FOLLOW_UP>\nuse the staging bucket\n</LATEST_FOLLOW_UP>");
	});

	test("the executor's budget notice is not a follow-up, so the parent's steer before it stays the latest", () => {
		const session = bind();
		// `buildBudgetNotice` in the fork's task/executor.ts, sent as a steer with the parent's own fields.
		const budgetNotice: Entry = {
			type: "message",
			message: {
				role: "user",
				content: [{ type: "text", text: "[budget notice] You have used 200 requests in this run (soft budget: 200). Wrap up now." }],
				steering: true,
				attribution: "agent",
			},
		};
		const branch = [sessionInit(ASSIGNMENT), user(ASSIGNMENT), parentSteer("also fix the second finding; report both shas"), budgetNotice, compaction];
		session.compact("sub", branch, true);
		const noticeOnly = bind();
		noticeOnly.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), budgetNotice, compaction], true);

		expect(session.sent[0].message.content).toContain(
			"<LATEST_FOLLOW_UP>\nalso fix the second finding; report both shas\n</LATEST_FOLLOW_UP>"
		);
		expect(session.sent[0].message.content).not.toContain("[budget notice]");
		expect(noticeOnly.sent[0].message.content).not.toContain("LATEST_FOLLOW_UP>");
	});

	test("a follow-up turn is restated, and the prompt that carried the assignment is not", () => {
		const followUp = bind();
		followUp.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), assistant("done"), user("now the docs"), compaction], true);
		const initialOnly = bind();
		initialOnly.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), assistant("working"), compaction], true);

		expect(followUp.sent[0].message.content).toContain("<LATEST_FOLLOW_UP>\nnow the docs\n</LATEST_FOLLOW_UP>");
		expect(initialOnly.sent[0].message.content).not.toContain("LATEST_FOLLOW_UP>");
	});

	test("a shaken user message does not reach the restatement", () => {
		const session = bind();
		session.compact("sub", [sessionInit(ASSIGNMENT), user(SHAKEN), compaction], true);

		expect(session.sent[0].message.content).toContain(ASSIGNMENT);
		expect(session.sent[0].message.content).not.toContain(SHAKEN);
	});

	test("a subagent whose branch has no session_init gets the reminder text alone", async () => {
		const session = bind();
		session.compact("sub", [user(ASSIGNMENT), ircIncoming(PARENT, "a follow-up"), compaction], true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toBe(await subagentLead());
	});

	test("no span shake would elide encloses the restated assignment or follow-up", () => {
		const session = bind();
		const followUp = `round 2.\n\n${"Keep every constraint. ".repeat(80)}`;
		session.compact("sub", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), ircIncoming(PARENT, followUp), compaction], true);
		const { content } = session.sent[0].message;

		const assignmentLine = content.split("\n").indexOf(ASSIGNMENT.split("\n")[0]);
		expect(assignmentLine).toBeGreaterThan(0);
		expect(content).toContain(followUp);
		expect(xmlSpansOf(content).filter(span => span.end >= assignmentLine)).toEqual([]);
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

	test("a successful shake leaves a marker with the context size before and after, in any session", () => {
		const sub = bind();
		sub.shakeStart("sub", 64_235);
		sub.shakeEnd("sub", [sessionInit(ASSIGNMENT)], false, { skipped: false }, 41_020);
		const main = bind();
		main.shakeStart("main", 851_384);
		main.shakeEnd("main", [], false, { skipped: false }, 402_117);

		expect(sub.entries).toEqual([["compaction-marker", { action: "shake", tokensBefore: 64_235, tokensAfter: 41_020 }]]);
		expect(main.entries).toEqual([["compaction-marker", { action: "shake", tokensBefore: 851_384, tokensAfter: 402_117 }]]);
	});

	test("a shake that drops nothing, falls through or aborts leaves no marker", () => {
		const session = bind();
		for (const outcome of [{ skipped: true }, { skipped: false, errorMessage: "still above the threshold" }, { aborted: true }]) {
			session.shakeStart("sub", 64_000);
			session.shakeEnd("sub", [sessionInit(ASSIGNMENT)], false, outcome, 63_000);
		}

		expect(session.entries).toEqual([]);
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

	test("a top-level session without an active [sdd] goal gets nothing, compaction or shake", () => {
		const session = bind();
		session.compact("main", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), compaction], true);
		const paused: Entry = { type: "mode_change", mode: "goal_paused" };
		session.compact("main", [sddGoal, paused, compaction], true);
		session.shakeEnd("main", [sddGoal, paused], true, { skipped: false });

		expect(session.sent).toHaveLength(0);
	});

	test("a top-level session with an active [sdd] goal gets the sdd reminder on every compaction and shake", async () => {
		const sddText = (await Bun.file(new URL("../compaction-reminder.md", import.meta.url)).text()).trim();
		const session = bind();
		const branch = [user(ASSIGNMENT), sddGoal, compaction];
		session.compact("main", branch, true);
		session.turnEnd(1);
		session.compact("main", [...branch, compaction], false);
		session.shakeEnd("main", [...branch, compaction], false, { skipped: false });

		expect(session.sent.map(s => [s.message.customType, s.message.content, s.options.deliverAs])).toEqual([
			["post-compaction-reminder", sddText, "nextTurn"],
			["post-compaction-reminder", sddText, "aside"],
			["post-compaction-reminder", sddText, "aside"],
		]);
	});
});
