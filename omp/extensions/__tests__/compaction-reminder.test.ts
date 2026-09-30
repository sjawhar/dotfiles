import { describe, expect, test } from "bun:test";
import extension from "../compaction-reminder";

type Pi = Parameters<typeof extension>[0];
type Sent = { message: { customType: string; content: string; display: boolean }; options: { deliverAs?: string } };
type Entry = Record<string, unknown> & { type: string };

const SESSION_DIR = "/home/u/.omp/agent/sessions/-src-repo/2026-09-30T01-02-03-456Z_01a0ef85-79c0-7197-9a12-31f588da3406";
const TOP_LEVEL_FILE = `${SESSION_DIR}.jsonl`;
const SUBAGENT_FILE = `${SESSION_DIR}/SubagentWorker.jsonl`;
const ASSIGNMENT = "# Target\n- `omp/config.yml` only\n\nNon-goals: the fork.\n\nReport the commit sha.";
const FENCED_ASSIGNMENT = `${ASSIGNMENT}\n\n\`\`\`text\n${"keep this exact block ".repeat(120)}\n\`\`\``;
const SHAKEN = "[shaken ~600 tokens — recover: artifact://7 (region 1)]";

const user = (content: unknown): Entry => ({ type: "message", message: { role: "user", content } });
const assistant = (text: string): Entry => ({ type: "message", message: { role: "assistant", content: [{ type: "text", text }] } });
const compaction: Entry = { type: "compaction", summary: "paraphrase" };
const sddGoal: Entry = { type: "mode_change", mode: "goal", data: { goal: { objective: "[sdd] ship it", status: "active" } } };
const context = (file: string, branch: Entry[], idle: boolean) => ({
	sessionManager: { getSessionFile: () => file, getBranch: () => branch },
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
		messageEnd: (message: unknown, branch: Entry[] = []) =>
			emit("message_end", { message }, context(SUBAGENT_FILE, branch, false)),
		compact: (file: string, branch: Entry[], idle: boolean) => emit("session_compact", {}, context(file, branch, idle)),
		shakeEnd: (file: string, branch: Entry[], idle: boolean, outcome: Record<string, unknown>) =>
			emit(
				"auto_compaction_end",
				{ action: "shake", result: undefined, aborted: false, willRetry: false, ...outcome },
				context(file, branch, idle)
			),
	};
}

const subagentLead = async () =>
	(await Bun.file(new URL("../compaction-reminder-subagent.md", import.meta.url)).text()).trim();

describe("compaction-reminder", () => {
	test("a subagent's first compaction restates its assignment verbatim after the reminder text", async () => {
		const session = bind();
		session.compact(SUBAGENT_FILE, [user([{ type: "text", text: ASSIGNMENT }]), assistant("working"), compaction], true);

		expect(session.sent).toHaveLength(1);
		const [{ message }] = session.sent;
		expect(message.customType).toBe("post-compaction-subagent-reminder");
		expect(message.display).toBe(true);
		expect(message.content.startsWith(await subagentLead())).toBe(true);
		expect(message.content).toContain(ASSIGNMENT);
	});

	test("the assignment is the first user message, joining its text parts", () => {
		const session = bind();
		const branch = [
			user([{ type: "text", text: "part one" }, { type: "image", data: "x" }, { type: "text", text: "part two" }]),
			assistant("working"),
			user([{ type: "text", text: "a later steer" }]),
			compaction,
		];
		session.compact(SUBAGENT_FILE, branch, true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toContain("part one\npart two");
		expect(session.sent[0].message.content).not.toContain("a later steer");
	});

	test("a subagent with no user message on its branch gets the reminder text alone", async () => {
		const session = bind();
		session.compact(SUBAGENT_FILE, [assistant("working"), compaction], true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toBe(await subagentLead());
	});

	test("a subagent's later compactions send nothing", () => {
		const session = bind();
		const branch = [user(ASSIGNMENT), compaction];
		session.compact(SUBAGENT_FILE, branch, true);
		session.compact(SUBAGENT_FILE, [...branch, compaction], true);

		expect(session.sent).toHaveLength(1);
	});

	test("each subagent binding gets its own first-compaction reminder", () => {
		const first = bind();
		const second = bind();
		first.compact(SUBAGENT_FILE, [user(ASSIGNMENT), compaction], true);
		second.compact(SUBAGENT_FILE, [user(ASSIGNMENT), compaction], true);

		expect(first.sent).toHaveLength(1);
		expect(second.sent).toHaveLength(1);
	});

	test("the reminder restates the assignment as it arrived, after a shake rewrote it on the branch", () => {
		const session = bind();
		session.messageEnd({ role: "user", content: [{ type: "text", text: FENCED_ASSIGNMENT }] });
		session.messageEnd({ role: "assistant", content: [{ type: "text", text: "working" }] });
		session.messageEnd({ role: "user", content: [{ type: "text", text: "a later steer" }] });
		const shakenBranch = [user([{ type: "text", text: `${ASSIGNMENT}\n\n${SHAKEN}` }]), user("a later steer"), compaction];
		session.compact(SUBAGENT_FILE, shakenBranch, true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toContain(FENCED_ASSIGNMENT);
		expect(session.sent[0].message.content).not.toContain(SHAKEN);
		expect(session.sent[0].message.content).not.toContain("a later steer");
	});

	test("a subagent woken again with a follow-up keeps the assignment already on its branch", () => {
		const session = bind();
		const branch = [user(ASSIGNMENT), assistant("done")];
		session.messageEnd({ role: "user", content: "a follow-up question" }, branch);
		session.compact(SUBAGENT_FILE, [...branch, user("a follow-up question"), compaction], true);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].message.content).toContain(ASSIGNMENT);
		expect(session.sent[0].message.content).not.toContain("a follow-up question");
	});

	test("a shake that dropped content is a subagent's first compaction", () => {
		const session = bind();
		session.messageEnd({ role: "user", content: ASSIGNMENT });
		session.turnEnd(1);
		session.shakeEnd(SUBAGENT_FILE, [user(SHAKEN)], false, { skipped: false });

		expect(session.sent.map(s => [s.message.customType, s.options.deliverAs])).toEqual([
			["post-compaction-subagent-reminder", "aside"],
		]);
		expect(session.sent[0].message.content).toContain(ASSIGNMENT);
		session.compact(SUBAGENT_FILE, [user(SHAKEN), compaction], false);
		expect(session.sent).toHaveLength(1);
	});

	test("a shake that drops nothing, falls through or aborts leaves the reminder to the compaction that follows", () => {
		const session = bind();
		const branch = [user(ASSIGNMENT)];
		const fellThrough = "Auto-shake reclaimed ~9000 tokens but context is still above the threshold; trying the next preferred compaction method.";
		session.shakeEnd(SUBAGENT_FILE, branch, true, { skipped: true });
		session.shakeEnd(SUBAGENT_FILE, branch, true, { skipped: false, errorMessage: fellThrough });
		session.shakeEnd(SUBAGENT_FILE, branch, true, { aborted: true });
		expect(session.sent).toHaveLength(0);

		session.compact(SUBAGENT_FILE, [...branch, compaction], true);
		expect(session.sent).toHaveLength(1);
	});

	test("a top-level session's shake sends no reminder, sdd goal or not", () => {
		const session = bind();
		session.shakeEnd(TOP_LEVEL_FILE, [user(ASSIGNMENT), sddGoal], true, { skipped: false });

		expect(session.sent).toHaveLength(0);
	});

	test("a subagent compacting mid-run gets the reminder as an aside", () => {
		const session = bind();
		session.turnEnd(2);
		session.compact(SUBAGENT_FILE, [user(ASSIGNMENT), compaction], false);

		expect(session.sent).toHaveLength(1);
		expect(session.sent[0].options.deliverAs).toBe("aside");
	});

	test("a subagent compacting while idle, or at the end of its run, gets the reminder on the next turn", () => {
		const idle = bind();
		idle.turnEnd(2);
		idle.compact(SUBAGENT_FILE, [user(ASSIGNMENT), compaction], true);
		const runEnd = bind();
		runEnd.turnEnd(0);
		runEnd.compact(SUBAGENT_FILE, [user(ASSIGNMENT), compaction], false);

		expect(idle.sent.map(s => s.options.deliverAs)).toEqual(["nextTurn"]);
		expect(runEnd.sent.map(s => s.options.deliverAs)).toEqual(["nextTurn"]);
	});

	test("a top-level session without an active [sdd] goal gets nothing", () => {
		const session = bind();
		session.compact(TOP_LEVEL_FILE, [user(ASSIGNMENT), assistant("working"), compaction], true);
		const paused: Entry = { type: "mode_change", mode: "goal_paused" };
		session.compact(TOP_LEVEL_FILE, [user(ASSIGNMENT), sddGoal, paused, compaction], true);

		expect(session.sent).toHaveLength(0);
	});

	test("a top-level session with an active [sdd] goal gets the sdd reminder on every compaction", async () => {
		const sddText = (await Bun.file(new URL("../compaction-reminder.md", import.meta.url)).text()).trim();
		const session = bind();
		const branch = [user(ASSIGNMENT), sddGoal, compaction];
		session.compact(TOP_LEVEL_FILE, branch, true);
		session.turnEnd(1);
		session.compact(TOP_LEVEL_FILE, [...branch, compaction], false);

		expect(session.sent.map(s => [s.message.customType, s.message.content, s.options.deliverAs])).toEqual([
			["post-compaction-reminder", sddText, "nextTurn"],
			["post-compaction-reminder", sddText, "aside"],
		]);
	});
});
