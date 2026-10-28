import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import extension from "../compaction-reminder";
import sddSkill from "../../../plugins/sjawhar/skills/sdd/SKILL.md" with { type: "text" };

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
/** A pasted sdd invocation as omp puts it on the branch: the skill's body, then the arguments. */
const SDD_INVOCATION = `${sddSkill.replace(/^---\n[\s\S]*?\n---\n/, "").trim()}\n\nship it`;
const sddInvocation = user(SDD_INVOCATION);
/** The entry omp persists when the user invokes `/skill:sdd`. */
const sddSkillPrompt: Entry = {
	type: "custom_message",
	customType: "skill-prompt",
	content: `[IMPORTANT: User invoked the "sdd" skill; follow its instructions. Full skill below.]\n\n${sddSkill}`,
	display: true,
};
/** A model-invoked load: the assistant's `read` tool call on the skill URI. */
const sddSkillRead: Entry = {
	type: "message",
	message: { role: "assistant", content: [{ type: "toolCall", name: "read", arguments: { path: "skill://sdd" } }] },
};
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
	sessionManager: { getBranch: () => branch, getSessionId: () => SESSION_ID },
	isIdle: () => idle,
	getContextUsage: () => (tokens === undefined ? undefined : { tokens, contextWindow: 1_000_000, percent: 0 }),
});

const SESSION_ID = "01a00000-0000-7000-8000-000000000001";
/** Every test runs with HOME at an empty temp dir, so the real `~/.config/omp/stream-goals.json` never leaks in. */
const HOME_DIR = mkdtempSync(join(tmpdir(), "compaction-reminder-home-"));
const MAP_DIR = join(HOME_DIR, ".config/omp");
const MAP_FILE = join(MAP_DIR, "stream-goals.json");
const savedHome = process.env.HOME;
beforeAll(() => {
	process.env.HOME = HOME_DIR;
});
afterAll(() => {
	process.env.HOME = savedHome;
	rmSync(HOME_DIR, { recursive: true, force: true });
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

	test("a top-level session that never invoked sdd gets nothing, compaction or shake", () => {
		const session = bind();
		session.compact("main", [sessionInit(ASSIGNMENT), user(ASSIGNMENT), compaction], true);
		// The skill text only quoted inside a message, or injected by the harness, is no invocation.
		const quoted = user(`Look at this:\n${SDD_INVOCATION}`);
		const synthetic: Entry = { type: "message", message: { role: "user", content: [{ type: "text", text: SDD_INVOCATION }], synthetic: true } };
		const otherSkill: Entry = { type: "custom_message", customType: "skill-prompt", content: '[IMPORTANT: User invoked the "analyze" skill; follow its instructions.]', display: true };
		const otherRead: Entry = { type: "message", message: { role: "assistant", content: [{ type: "toolCall", name: "read", arguments: { path: "skill://analyze" } }] } };
		session.compact("main", [quoted, synthetic, otherSkill, otherRead, compaction], true);
		session.shakeEnd("main", [quoted, synthetic, otherSkill, otherRead], true, { skipped: false });

		expect(session.sent).toHaveLength(0);
	});

	test.each([
		["pasted body", () => sddInvocation],
		["/skill:sdd", () => sddSkillPrompt],
		["read skill://sdd", () => sddSkillRead],
	])("a top-level session that invoked sdd by %s gets the sdd reminder on every compaction and shake", async (_name, invocation) => {
		const sddText = (await Bun.file(new URL("../compaction-reminder.md", import.meta.url)).text()).trim();
		const session = bind();
		const branch = [user(ASSIGNMENT), invocation(), compaction];
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

describe("compaction-reminder: a stream lead's goals document", () => {
	const GOALS = "dispatch://OSSV/artifact/goals-md";
	/** Run `body` with STREAM_GOALS set to `value` (undefined unsets it), restoring the caller's value after. */
	const withGoals = (value: string | undefined, body: () => void) => {
		const saved = process.env.STREAM_GOALS;
		if (value === undefined) delete process.env.STREAM_GOALS;
		else process.env.STREAM_GOALS = value;
		try {
			body();
		} finally {
			if (saved === undefined) delete process.env.STREAM_GOALS;
			else process.env.STREAM_GOALS = saved;
		}
	};

	test("a top-level session started with STREAM_GOALS is told to read it on every compaction and shake", () => {
		withGoals(GOALS, () => {
			const session = bind();
			const branch = [user("lead the stream"), compaction];
			session.compact("main", branch, true);
			session.turnEnd(1);
			session.shakeEnd("main", branch, false, { skipped: false });

			expect(session.sent.map(s => [s.message.customType, s.options.deliverAs])).toEqual([
				["post-compaction-stream-goals", "nextTurn"],
				["post-compaction-stream-goals", "aside"],
			]);
			for (const s of session.sent) expect(s.message.content).toContain(GOALS);
		});
	});

	test("the goals reminder comes before the sdd reminder when both apply", () => {
		withGoals(GOALS, () => {
			const session = bind();
			session.compact("main", [user(ASSIGNMENT), sddInvocation, compaction], true);

			expect(session.sent.map(s => s.message.customType)).toEqual(["post-compaction-stream-goals", "post-compaction-reminder"]);
		});
	});

	test("a subagent never gets the goals reminder, even though it inherits STREAM_GOALS", () => {
		withGoals(GOALS, () => {
			const session = bind();
			session.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);
			session.compact("sub", [sessionInit(ASSIGNMENT), compaction, compaction], true);

			expect(session.sent.map(s => s.message.customType)).toEqual(["post-compaction-subagent-reminder"]);
		});
	});

	test.each([["unset", undefined], ["blank", "  "]])("STREAM_GOALS %s gives no goals reminder", (_name, value) => {
		withGoals(value, () => {
			const session = bind();
			session.compact("main", [user("lead the stream"), compaction], true);

			expect(session.sent).toHaveLength(0);
		});
	});

	describe("the session-id map in ~/.config/omp/stream-goals.json", () => {
		const MAPPED = "dispatch://LINUX-31/spec";
		/** Run `body` with the map file holding `content` (undefined: no file), STREAM_GOALS unset unless given. */
		const withMap = (content: string | undefined, body: () => void, env?: string) => {
			rmSync(MAP_DIR, { recursive: true, force: true });
			if (content !== undefined) {
				mkdirSync(MAP_DIR, { recursive: true });
				writeFileSync(MAP_FILE, content);
			}
			try {
				withGoals(env, body);
			} finally {
				rmSync(MAP_DIR, { recursive: true, force: true });
			}
		};

		test("a mapped top-level session is told to read its mapped document on compaction and shake", () => {
			withMap(JSON.stringify({ [SESSION_ID]: MAPPED, other: "dispatch://X/spec" }), () => {
				const session = bind();
				const branch = [user("lead the stream"), compaction];
				session.compact("main", branch, true);
				session.turnEnd(1);
				session.shakeEnd("main", branch, false, { skipped: false });

				expect(session.sent.map(s => s.message.customType)).toEqual([
					"post-compaction-stream-goals",
					"post-compaction-stream-goals",
				]);
				for (const s of session.sent) expect(s.message.content).toContain(MAPPED);
			});
		});

		test("STREAM_GOALS wins over a map entry", () => {
			withMap(
				JSON.stringify({ [SESSION_ID]: MAPPED }),
				() => {
					const session = bind();
					session.compact("main", [user("lead the stream"), compaction], true);

					expect(session.sent).toHaveLength(1);
					expect(session.sent[0].message.content).toContain(GOALS);
					expect(session.sent[0].message.content).not.toContain(MAPPED);
				},
				GOALS
			);
		});

		test("a subagent never gets the mapped reminder", () => {
			withMap(JSON.stringify({ [SESSION_ID]: MAPPED }), () => {
				const session = bind();
				session.compact("sub", [sessionInit(ASSIGNMENT), compaction], true);

				expect(session.sent.map(s => s.message.customType)).toEqual(["post-compaction-subagent-reminder"]);
			});
		});

		test.each([
			["no map file", undefined],
			["no entry for the session", JSON.stringify({ other: MAPPED })],
			["a malformed file", "{not json"],
			["a non-object file", JSON.stringify([MAPPED])],
			["a non-string entry", JSON.stringify({ [SESSION_ID]: 7 })],
		])("%s gives no goals reminder and does not throw", (_name, content) => {
			withMap(content, () => {
				const session = bind();
				expect(() => session.compact("main", [user("lead the stream"), compaction], true)).not.toThrow();
				expect(session.sent).toHaveLength(0);
			});
		});
	});

	describe("a goals document that is a local file of at most 16 KB is inlined", () => {
		const DOC_DIR = join(HOME_DIR, "goals");
		const DOC = join(DOC_DIR, "decisions.md");
		const TEXT = "# Decisions\n\n- Owners write Lean in their own session.\n- No restart of the 343 owners.\n";
		const MTIME = new Date("2026-10-28T03:56:52.000Z");
		/** Write the goals file with `text` and the fixed mtime, run `body`, then remove it. */
		const withDoc = (text: string, body: () => void) => {
			mkdirSync(DOC_DIR, { recursive: true });
			writeFileSync(DOC, text);
			utimesSync(DOC, MTIME, MTIME);
			try {
				body();
			} finally {
				rmSync(DOC_DIR, { recursive: true, force: true });
			}
		};
		const OPEN = `<STREAM_GOALS_DOCUMENT path="${DOC}" mtime="${MTIME.toISOString()}">`;

		test("the reminder carries the file's full text in an uppercase wrapper naming its path and mtime", () => {
			withDoc(TEXT, () =>
				withGoals(DOC, () => {
					const session = bind();
					session.compact("main", [user("lead the stream"), compaction], true);

					expect(session.sent).toHaveLength(1);
					const { customType, content } = session.sent[0].message;
					expect(customType).toBe("post-compaction-stream-goals");
					expect(content).toContain(`${OPEN}\n${TEXT}</STREAM_GOALS_DOCUMENT>`);
					expect(content.indexOf(OPEN)).toBeGreaterThan(0);
					expect(content.trimEnd().endsWith("</STREAM_GOALS_DOCUMENT>")).toBe(true);
				})
			);
		});

		test("a file named by the session-id map is inlined too", () => {
			withDoc(TEXT, () => {
				mkdirSync(MAP_DIR, { recursive: true });
				writeFileSync(MAP_FILE, JSON.stringify({ [SESSION_ID]: DOC }));
				try {
					withGoals(undefined, () => {
						const session = bind();
						session.compact("main", [user("lead the stream"), compaction], true);
						expect(session.sent[0].message.content).toContain(`${OPEN}\n${TEXT}</STREAM_GOALS_DOCUMENT>`);
					});
				} finally {
					rmSync(MAP_DIR, { recursive: true, force: true });
				}
			});
		});

		test("a ~/ path is read from HOME", () => {
			withDoc(TEXT, () =>
				withGoals("~/goals/decisions.md", () => {
					const session = bind();
					session.compact("main", [user("lead the stream"), compaction], true);
					expect(session.sent[0].message.content).toContain(`${OPEN}\n${TEXT}</STREAM_GOALS_DOCUMENT>`);
				})
			);
		});

		test("the file is read at each compaction, so an edit reaches the next reminder", () => {
			withDoc(TEXT, () =>
				withGoals(DOC, () => {
					const session = bind();
					const branch = [user("lead the stream"), compaction];
					session.compact("main", branch, true);
					writeFileSync(DOC, "# Decisions\n\n- A later ruling.\n");
					session.turnEnd(1);
					session.shakeEnd("main", branch, false, { skipped: false });

					expect(session.sent.map(s => s.options.deliverAs)).toEqual(["nextTurn", "aside"]);
					expect(session.sent[0].message.content).toContain("No restart of the 343 owners.");
					expect(session.sent[1].message.content).toContain("- A later ruling.\n</STREAM_GOALS_DOCUMENT>");
					expect(session.sent[1].message.content).not.toContain("No restart of the 343 owners.");
				})
			);
		});

		test("exactly 16 KB is inlined; one byte more keeps the pointer", () => {
			for (const [size, inlined] of [
				[16 * 1024, true],
				[16 * 1024 + 1, false],
			] as const) {
				const text = `${"x".repeat(size - 1)}\n`;
				withDoc(text, () =>
					withGoals(DOC, () => {
						const session = bind();
						session.compact("main", [user("lead the stream"), compaction], true);
						const { content } = session.sent[0].message;
						expect(content.includes(text)).toBe(inlined);
						expect(content.includes("STREAM_GOALS_DOCUMENT")).toBe(inlined);
						expect(content).toContain(DOC);
					})
				);
			}
		});

		test.each([
			["a dispatch:// URL", "dispatch://OSSV/artifact/goals-md"],
			["a missing file", join(HOME_DIR, "goals", "missing.md")],
			["a directory", HOME_DIR],
			["free text naming a document", "dispatch://OSSV/artifact/goals-md (read with: dispatch doc-read)"],
		])("%s keeps the pointer to read it", (_name, target) => {
			withGoals(target, () => {
				const session = bind();
				session.compact("main", [user("lead the stream"), compaction], true);
				const { content } = session.sent[0].message;
				expect(content).toContain(`read your stream's goals document in full: ${target}`);
				expect(content).not.toContain("STREAM_GOALS_DOCUMENT");
			});
		});

		test("no span shake would elide encloses the inlined document", () => {
			const large = `# Decisions\n\n${"Each owner keeps its own record and never edits another's. ".repeat(60)}\n`;
			withDoc(large, () =>
				withGoals(DOC, () => {
					const session = bind();
					session.compact("main", [user("lead the stream"), compaction], true);
					const { content } = session.sent[0].message;

					const docLine = content.split("\n").indexOf("# Decisions");
					expect(docLine).toBeGreaterThan(0);
					expect(xmlSpansOf(content).filter(span => span.end >= docLine)).toEqual([]);
				})
			);
		});
	});
});
