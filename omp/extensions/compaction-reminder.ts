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
// history. Shake never reaches it: it elides old tool output in place and
// commits no compaction entry, and since it paraphrases nothing there is
// nothing to restate. Each reminder is a persisted, displayed custom message
// either way; only the queue differs, because omp has no single delivery that
// fits every moment a compaction can land:
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
// `turn_end` reliably precedes `session_compact` at the same boundary (it is
// pushed before the loop's onTurnEnd hook that hosts mid-turn maintenance),
// which is what makes the last turn's tool-call count a valid signal here.
//
// Subagents rebind the parent's extensions to their own runtime, so this
// handler also runs for their compactions. The transcript name tells the two
// apart: subagent transcripts live inside the parent's session directory as
// <AgentName>.jsonl rather than a <timestamp>_<uuid>.jsonl, the grammar
// session-env.ts uses for the same distinction. Only the top-level session is
// the coordinator following the sdd process, so the sdd reminder is for it
// alone. A subagent instead gets its assignment back: the summary paraphrases
// the scope limits, forbidden files and required output it was dispatched
// with, so on its first compaction it is told to re-check them and handed the
// first user message on its branch (the assignment) verbatim, or the reminder
// text alone when the branch has none. Later compactions stay quiet: one
// restatement per subagent, not one per summary. State lives in the factory
// closure, which is per session binding, never at module scope shared across
// sessions.
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
type MessageEntry = {
	type: "message";
	message: { role: string; content: string | Array<{ type: string; text?: string }> };
};
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

/** Text of the branch's first user message: the assignment a subagent was dispatched with. */
function firstUserText(ctx: CompactCtx): string | undefined {
	for (const entry of ctx.sessionManager.getBranch()) {
		if (entry.type !== "message") continue;
		const { message } = entry as MessageEntry;
		if (message.role !== "user") continue;
		if (typeof message.content === "string") return message.content;
		return message.content
			.filter(part => part.type === "text")
			.map(part => part.text)
			.join("\n");
	}
	return undefined;
}

export default function (pi: ExtensionAPI) {
	let runContinues = false;
	let subagentReminded = false;

	pi.on("turn_end", (event: { toolResults: unknown[] }) => {
		runContinues = event.toolResults.length > 0;
	});
	pi.on("agent_end", () => {
		runContinues = false;
	});
	pi.on("session_compact", (_event: unknown, ctx: CompactCtx) => {
		const file = ctx.sessionManager.getSessionFile();
		if (!file) return;
		let message = SDD_MESSAGE;
		if (TOP_LEVEL_TRANSCRIPT.test(path.basename(file))) {
			if (!sddGoalActive(ctx)) return;
		} else {
			if (subagentReminded) return;
			subagentReminded = true;
			const assignment = firstUserText(ctx);
			message = {
				customType: "post-compaction-subagent-reminder",
				content: assignment ? `${SUBAGENT_REMINDER}\n\n<assignment>\n${assignment}\n</assignment>` : SUBAGENT_REMINDER,
				display: true,
			};
		}
		pi.sendMessage(message, { deliverAs: !ctx.isIdle() && runContinues ? "aside" : "nextTurn" });
	});
}
