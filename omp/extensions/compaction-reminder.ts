// Re-assert what a compaction loses, in two cases: the sdd process contract for
// a top-level session while an sdd goal is active, and what a subagent was told
// to do, on its first compaction. Every successful automatic shake also leaves
// a `compaction-marker` entry recording the context size before and after it.
//
// A compaction summary paraphrases the conversation; a workflow contract the
// session was following (the sdd command's agent mapping and gates) rarely
// survives that intact, and the model has no cue that it should re-read it.
// Firing on every compaction of every session was measured as noise: over 15
// sessions that invoked /sdd and 268 compactions, the reminder led to a re-read
// of sdd.md within 60 turns 22 times (8%), and most sessions were not running
// sdd at all. So the reminder is bound to a goal: sdd.md has the coordinator
// create a goal whose objective starts `[sdd]`, and this handler fires only
// while such a goal is active. The goal is read from the session branch at
// compaction time rather than tracked from `goal_updated` events, because a
// resumed session restores its goal from the persisted `mode_change` entry
// without re-emitting the event. The last `mode_change` on the branch decides:
// `mode: "goal"` with `data.goal.status === "active"` and the `[sdd]` prefix
// arms it; `goal_paused`, `none`, or a complete/dropped status disarms it.
//
// Two events carry a compaction. `session_compact` fires after every committed
// one — auto, /compact, remote, snapcompact, handoff, soft — once the summary
// has replaced the history. A shake commits nothing: it swaps old tool output,
// and fenced or XML blocks of 400+ tokens in any message, for recovery
// placeholders in place, and reports only through `auto_compaction_end` with
// action `shake`. That end counts here only when the shake dropped content and
// brought the context back under its threshold (not aborted, not skipped, no
// errorMessage); a shake that fell through leaves it to the method that commits
// after it. Both reminders fire on both events: a shake can drop the
// coordinator's re-read of sdd.md, a plain file read, as readily as a summary
// can. A successful shake also appends a `compaction-marker` custom entry,
// never sent to the model, with the context size before and after it, in every
// session: it is the only record of the size a shake leaves, for any audit of
// what compaction costs (`compaction-event-audit.py` on AGENTC-1288 is one
// reader, counting markers beside committed compaction entries). The fork
// logs the trigger size it decided on at debug level ("Mid-run compaction ran
// between provider calls", "Pre-prompt context maintenance triggered …"), but
// does not pass it to extensions — `auto_compaction_start` carries only
// `reason` and `action` — so both marker figures are `ctx.getContextUsage()`,
// the session's anchored estimate (the last provider-reported prompt size plus
// a local count of what came after it): `tokensBefore` read at
// `auto_compaction_start`, `tokensAfter` after the shake, which folds its own
// savings into that estimate. The fork's trigger figure is computed differently
// (from the last response's billed usage and a stored-context estimate), so
// `tokensBefore` need not equal the logged number. Each reminder is a
// persisted, displayed custom message; only the queue differs, because omp has
// no single delivery that fits every moment a compaction can land:
//
//   idle (/compact, idle compaction): `nextTurn` appends to context at once.
//   mid-run (compaction at a tool-loop boundary and the turn just ended with
//     tool calls, so another provider request follows): `aside`. The agent
//     loop polls asides at that same boundary, so the reminder rides the very
//     next request and stays in context for the rest of the run. `nextTurn`
//     here would sit in the hidden queue until the next user prompt while the
//     model kept working blind.
//   run end (the turn ended without tool calls, so the loop is stopping):
//     `nextTurn`, drained into the next prompt. An `aside` here would be found
//     by the loop's stop-boundary poll and force an extra model turn whose only
//     input is this reminder.
//
// `turn_end` reliably precedes `session_compact` and `auto_compaction_end` at
// the same boundary (it is pushed before the loop's onTurnEnd hook that hosts
// mid-turn maintenance), which is what makes the last turn's tool-call count a
// valid signal here.
//
// Subagents rebind the parent's extensions to their own runtime, so these
// handlers also run for their compactions. `ctx.agent.kind` tells the two
// apart: `"sub"` for anything spawned — a task subagent, an eval agent, a
// `/tan` clone — and `"main"` for the top-level session. Only the top-level
// session is the coordinator following the sdd process, so the sdd reminder is
// for it alone. A subagent instead gets back what it was told: a summary
// paraphrases the scope limits, forbidden files and required output, and a
// shake can swap a large block of them for a placeholder. So on its first
// compaction it is told to re-check them, and handed, verbatim, the assignment
// it was spawned with and the latest instruction it received after it.
// Later compactions stay quiet: one restatement per session binding. A cold
// revive rebinds fresh extension instances, so a revived subagent gets one
// more, which then carries the follow-up that woke it. State lives in the
// factory closure, never at module scope shared across sessions.
//
// The assignment is the `task` of the branch's `session_init` (the latest,
// should there be more than one): the string the runtime recorded before
// prompting this session with it. A task subagent records it before its first
// prompt, and a `/tan` clone, which forks its parent's whole transcript,
// records its own after the fork — so in a clone the first user message on the
// branch is the parent's prompt, and only `session_init` is this session's
// work. Shake scans only `message` and `custom_message` entries, so
// `session_init` also survives every shake and a cold revive. With no
// `session_init` — nothing spawned through the task executor or `/tan`
// recorded one — the reminder text goes alone.
//
// The assignment is shown for its constraints, not as the current request:
// most subagents reach their first compaction while working on a follow-up,
// and a follow-up never writes a `session_init`. The follow-up restated is the
// latest, after that `session_init`, of: an `irc:incoming` custom message from
// `ctx.agent.parentId` (the parent's message to an idle agent; its raw body is
// `details.message`); any user message flagged `steering`, which is a message
// to a running agent (the parent's IRC message, a person's, or any other
// queued steer, such as an extension's `sendUserMessage` with `deliverAs:
// "steer"`), except the executor's soft-request-budget notice; and a user message
// that is neither synthetic, a steer, nor the prompt that carried the
// assignment (a follow-up turn). A steer counts by its flag; the fork's
// parent-irc template only shapes its text, so when the text still carries that
// wrapper it is unwrapped, and otherwise — a reworded template, or an `<irc>`
// block a shake already swapped for a placeholder — it is restated as it stands.
// The budget notice is a runtime warning, not a ruling on the task, and the
// executor enforces the budget itself; persisted, it carries the same role,
// `steering` flag and attribution as a parent's IRC steer, so it is told apart
// by the fork's `[budget notice] ` prefix. A reworded notice would only be
// restated again, never drop an instruction. The root fix is in the fork: send
// the notice as a hidden custom message, as the goal runtime sends its
// `goal-budget-limit`, and this skip goes.
// The lead says a later instruction wins where the two conflict. Only the latest
// one is restated: earlier follow-ups survive through the summary, so an
// intermediate ruling that changed a spawn constraint is recalled only as well
// as the summary keeps it, though the lead still ranks it above the original.
//
// A `/tan` clone's committed compaction is restated here and, when the summary
// dropped the request, by the fork's own `/tan` restore as well. The lead's
// "not a new request" framing makes that duplicate harmless, and no compacted
// clone showed up in a 14-day sample, so it is accepted rather than special-
// cased.
//
// The restatement is wrapped in `<ORIGINAL_ASSIGNMENT>` and `<LATEST_FOLLOW_UP>`
// rather than lowercase tags: shake reads a lowercase tag alone on its line as
// one top-level XML span, and would elide a whole 400+ token restatement as a
// single region the next time it ran. Its opening grammar is lowercase-only, so
// an uppercase name never matches. A large fenced or lowercase-XML block
// *inside* the restated text is still a region of its own.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import sddReminder from "./compaction-reminder.md" with { type: "text" };
import subagentReminder from "./compaction-reminder-subagent.md" with { type: "text" };

const SDD_REMINDER = sddReminder.trim();
const SUBAGENT_REMINDER = subagentReminder.trim();
const SDD_PREFIX = "[sdd]";
/** The fork's `prompts/steering/parent-irc.md` wrapper, stripped from a steer when present. */
const PARENT_STEER_PREFIX = "[Wait interrupted by message]\n";
const PARENT_STEER_BODY = /^<irc from="parent" agent="[^"]*">\n([\s\S]*)\n<\/irc>$/;
/** The fork's `buildBudgetNotice` (task/executor.ts): a runtime steer, not an instruction. */
const BUDGET_NOTICE_PREFIX = "[budget notice] ";

type BranchEntry = { type: string };
type ModeChangeEntry = {
	type: "mode_change";
	mode: string;
	data?: { goal?: { objective?: string; status?: string } };
};
type InitEntry = { type: "session_init"; task: string };
type BranchMessage = {
	role: string;
	content: string | Array<{ type: string; text?: string }>;
	steering?: boolean;
	synthetic?: boolean;
};
type MessageEntry = { type: "message"; message: BranchMessage };
type IrcEntry = { type: "custom_message"; customType: string; details?: { from?: string; message?: string } };
type ShakeEndEvent = { action: string; aborted: boolean; skipped?: boolean; errorMessage?: string };
type CompactCtx = {
	agent: { kind: "main" | "sub"; parentId?: string };
	sessionManager: { getBranch: () => BranchEntry[] };
	isIdle: () => boolean;
	getContextUsage: () => { tokens: number } | undefined;
};

/** True while the branch's latest mode change is an active goal whose objective starts `[sdd]`. */
function sddGoalActive(ctx: CompactCtx): boolean {
	const branch = ctx.sessionManager.getBranch();
	for (let i = branch.length - 1; i >= 0; i--) {
		const entry = branch[i];
		if (entry.type !== "mode_change") continue;
		const mode = entry as ModeChangeEntry;
		if (mode.mode !== "goal") return false;
		const goal = mode.data?.goal;
		return goal?.status === "active" && (goal.objective ?? "").trimStart().startsWith(SDD_PREFIX);
	}
	return false;
}

/** Text parts of a message, joined. */
function messageText(message: BranchMessage): string {
	if (typeof message.content === "string") return message.content;
	return message.content
		.filter(part => part.type === "text")
		.map(part => part.text)
		.join("\n");
}

/** The latest instruction received after the assignment, as the branch now holds it. */
function latestFollowUp(entries: BranchEntry[], parentId: string | undefined): string | undefined {
	let latest: string | undefined;
	let assignmentPrompt = true;
	for (const entry of entries) {
		if (entry.type === "custom_message") {
			const { customType, details } = entry as IrcEntry;
			if (customType === "irc:incoming" && details?.from === parentId) latest = details.message ?? latest;
			continue;
		}
		if (entry.type !== "message") continue;
		const { message } = entry as MessageEntry;
		if (message.role !== "user" || message.synthetic) continue;
		if (assignmentPrompt && !message.steering) {
			// The first plain user message after `session_init` is the prompt that carried the assignment.
			assignmentPrompt = false;
			continue;
		}
		const text = messageText(message);
		// A reworded notice only gets restated, as before; no instruction is ever dropped.
		if (message.steering && text.startsWith(BUDGET_NOTICE_PREFIX)) continue;
		const rest = text.startsWith(PARENT_STEER_PREFIX) ? text.slice(PARENT_STEER_PREFIX.length) : text;
		latest = PARENT_STEER_BODY.exec(rest)?.[1] ?? rest;
	}
	return latest;
}

/** The subagent reminder: the lead, then the assignment and the latest follow-up when on record. */
function subagentRestatement(ctx: CompactCtx): string {
	const branch = ctx.sessionManager.getBranch();
	const initIndex = branch.findLastIndex(entry => entry.type === "session_init");
	if (initIndex < 0) return SUBAGENT_REMINDER;
	const { task } = branch[initIndex] as InitEntry;
	const followUp = latestFollowUp(branch.slice(initIndex + 1), ctx.agent.parentId);
	const blocks = [SUBAGENT_REMINDER];
	if (task) blocks.push(`<ORIGINAL_ASSIGNMENT>\n${task}\n</ORIGINAL_ASSIGNMENT>`);
	if (followUp) blocks.push(`<LATEST_FOLLOW_UP>\n${followUp}\n</LATEST_FOLLOW_UP>`);
	return blocks.join("\n\n");
}

export default function (pi: ExtensionAPI) {
	let runContinues = false;
	let subagentReminded = false;
	let shakeStartTokens: number | undefined;

	const deliver = (customType: string, content: string, ctx: CompactCtx) =>
		pi.sendMessage(
			{ customType, content, display: true },
			{ deliverAs: !ctx.isIdle() && runContinues ? "aside" : "nextTurn" }
		);
	const remind = (ctx: CompactCtx) => {
		if (ctx.agent.kind === "main") {
			if (sddGoalActive(ctx)) deliver("post-compaction-reminder", SDD_REMINDER, ctx);
			return;
		}
		if (subagentReminded) return;
		subagentReminded = true;
		deliver("post-compaction-subagent-reminder", subagentRestatement(ctx), ctx);
	};

	pi.on("turn_end", (event: { toolResults: unknown[] }) => {
		runContinues = event.toolResults.length > 0;
	});
	pi.on("agent_end", () => {
		runContinues = false;
	});
	pi.on("session_compact", (_event: unknown, ctx: CompactCtx) => remind(ctx));
	pi.on("auto_compaction_start", (event: { action: string }, ctx: CompactCtx) => {
		if (event.action === "shake") shakeStartTokens = ctx.getContextUsage()?.tokens;
	});
	pi.on("auto_compaction_end", (event: ShakeEndEvent, ctx: CompactCtx) => {
		// A shake that fell through carries an errorMessage; the method after it commits and reminds.
		if (event.action !== "shake" || event.aborted || event.skipped || event.errorMessage !== undefined) return;
		pi.appendEntry("compaction-marker", {
			action: event.action,
			tokensBefore: shakeStartTokens,
			tokensAfter: ctx.getContextUsage()?.tokens,
		});
		shakeStartTokens = undefined;
		remind(ctx);
	});
}
