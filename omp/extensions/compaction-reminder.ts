// Re-assert what a compaction summary loses, in two cases: the sdd process
// contract for a top-level session while an sdd goal is active, and the
// original assignment for a subagent on its first compaction.
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
// `session_compact` fires after every committed compaction — auto, /compact,
// remote, snapcompact, handoff, soft — once the summary has replaced the
// history. Shake never reaches it: it replaces old tool output, and fenced or
// XML blocks of 400+ tokens in any message, with recovery placeholders in
// place and commits no compaction entry, so an automatic shake reports only
// through `auto_compaction_end` with action `shake`. Each reminder is a
// persisted, displayed custom message either way; only the queue differs,
// because omp has no single delivery that fits every moment a compaction can
// land:
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
// handlers also run for their compactions. The transcript name tells the two
// apart: subagent transcripts live inside the parent's session directory as
// <AgentName>.jsonl rather than a <timestamp>_<uuid>.jsonl, the grammar
// session-env.ts uses for the same distinction. Only the top-level session is
// the coordinator following the sdd process, so the sdd reminder is for it
// alone, on committed compactions. A subagent instead gets its assignment
// back: a summary paraphrases the scope limits, forbidden files and required
// output it was dispatched with, and a shake can swap a large block of the
// assignment itself for a placeholder. So on its first compaction — a
// committed one, or an automatic shake that dropped content and brought the
// context back under its threshold — it is told to re-check them and handed
// the assignment verbatim. A shake that dropped nothing, aborted, or fell
// through to the next method leaves the reminder to the compaction that
// follows. Later compactions stay quiet: one restatement per subagent, not one
// per summary.
//
// The assignment is the first user message on the branch, recorded at the
// binding's first user `message_end`, before any compaction can have touched
// it: the branch's first user message when the branch already has one (a
// revived subagent woken with a follow-up keeps what it was dispatched with),
// else the message just sent. Only when nothing was recorded is the branch
// read at compaction time; with no user message at all the reminder text goes
// alone. State lives in the factory closure, which is per session binding,
// never at module scope shared across sessions.
import path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import sddReminder from "./compaction-reminder.md" with { type: "text" };
import subagentReminder from "./compaction-reminder-subagent.md" with { type: "text" };

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const TOP_LEVEL_TRANSCRIPT = new RegExp(`^\\d{4}-\\d{2}-\\d{2}T[\\d-]+Z_${UUID}\\.jsonl$`);
const SDD_MESSAGE = { customType: "post-compaction-reminder", content: sddReminder.trim(), display: true };
const SUBAGENT_REMINDER = subagentReminder.trim();
const SDD_PREFIX = "[sdd]";

type ModeChangeEntry = {
	type: "mode_change";
	mode: string;
	data?: { goal?: { objective?: string; status?: string } };
};
type UserMessage = { role: string; content: string | Array<{ type: string; text?: string }> };
type MessageEntry = { type: "message"; message: UserMessage };
type ShakeEndEvent = { action: string; aborted: boolean; skipped?: boolean; errorMessage?: string };
type CompactCtx = {
	sessionManager: { getSessionFile: () => string | undefined; getBranch: () => Array<{ type: string }> };
	isIdle: () => boolean;
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
function messageText(message: UserMessage): string {
	if (typeof message.content === "string") return message.content;
	return message.content
		.filter(part => part.type === "text")
		.map(part => part.text)
		.join("\n");
}

/** Text of the branch's first user message: the assignment a subagent was dispatched with. */
function firstUserText(ctx: CompactCtx): string | undefined {
	for (const entry of ctx.sessionManager.getBranch()) {
		if (entry.type !== "message") continue;
		const { message } = entry as MessageEntry;
		if (message.role === "user") return messageText(message);
	}
	return undefined;
}

/** Whether the session is a subagent; undefined when it has no transcript to tell by. */
function isSubagent(ctx: CompactCtx): boolean | undefined {
	const file = ctx.sessionManager.getSessionFile();
	if (!file) return undefined;
	return !TOP_LEVEL_TRANSCRIPT.test(path.basename(file));
}

export default function (pi: ExtensionAPI) {
	let runContinues = false;
	let assignment: string | undefined;
	let subagentReminded = false;

	const deliver = (message: typeof SDD_MESSAGE, ctx: CompactCtx) =>
		pi.sendMessage(message, { deliverAs: !ctx.isIdle() && runContinues ? "aside" : "nextTurn" });
	const remindSubagent = (ctx: CompactCtx) => {
		if (subagentReminded) return;
		subagentReminded = true;
		const text = assignment ?? firstUserText(ctx);
		deliver(
			{
				customType: "post-compaction-subagent-reminder",
				content: text ? `${SUBAGENT_REMINDER}\n\n<assignment>\n${text}\n</assignment>` : SUBAGENT_REMINDER,
				display: true,
			},
			ctx
		);
	};

	pi.on("turn_end", (event: { toolResults: unknown[] }) => {
		runContinues = event.toolResults.length > 0;
	});
	pi.on("agent_end", () => {
		runContinues = false;
	});
	pi.on("message_end", (event: { message: UserMessage }, ctx: CompactCtx) => {
		if (assignment !== undefined || event.message.role !== "user") return;
		assignment = firstUserText(ctx) ?? messageText(event.message);
	});
	pi.on("session_compact", (_event: unknown, ctx: CompactCtx) => {
		const subagent = isSubagent(ctx);
		if (subagent) remindSubagent(ctx);
		else if (subagent === false && sddGoalActive(ctx)) deliver(SDD_MESSAGE, ctx);
	});
	pi.on("auto_compaction_end", (event: ShakeEndEvent, ctx: CompactCtx) => {
		// A shake that fell through carries an errorMessage; the method after it commits and reminds.
		if (event.action !== "shake" || event.aborted || event.skipped || event.errorMessage !== undefined) return;
		if (isSubagent(ctx)) remindSubagent(ctx);
	});
}
