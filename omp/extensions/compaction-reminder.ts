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
// handlers also run for their compactions. `ctx.agent.kind` tells the two
// apart: `"sub"` for anything spawned — a task subagent, an eval agent, a
// `/tan` clone — and `"main"` for the top-level session. Only the top-level
// session is the coordinator following the sdd process, so the sdd reminder is
// for it alone, on committed compactions. A subagent instead gets its
// assignment back: a summary paraphrases the scope limits, forbidden files and
// required output it was dispatched with, and a shake can swap a large block
// of the assignment itself for a placeholder. So on its first compaction — a
// committed one, or an automatic shake that dropped content and brought the
// context back under its threshold — it is told to re-check them and handed
// the assignment verbatim. A shake that dropped nothing, aborted, or fell
// through to the next method leaves the reminder to the compaction that
// follows. Later compactions stay quiet: one restatement per subagent, not one
// per summary. State lives in the factory closure, which is per session
// binding, never at module scope shared across sessions.
//
// The assignment is the `task` of the branch's latest `session_init`: the
// string the runtime recorded before prompting this session with it, whoever
// spawned it. The latest one is this session's own — a `/tan` clone forks its
// parent's transcript, so the parent's entry is on the branch too, and the
// clone's own follows it. Shake scans only `message` and `custom_message`
// entries, so `session_init` survives every shake and a cold revive; the first
// user message on the branch survives neither, and in a `/tan` clone is the
// parent's prompt rather than this session's work. With no `session_init` —
// nothing spawned through the task executor recorded one — the reminder text
// goes alone.
//
// The restatement is wrapped in `<ORIGINAL_ASSIGNMENT>` rather than a
// lowercase tag: shake reads a lowercase tag alone on its line as one
// top-level XML span, and would elide the whole restated assignment as a
// single 400+ token region the next time it ran. Its opening grammar is
// lowercase-only, so an uppercase name never matches. A large fenced or
// lowercase-XML block *inside* the assignment is still a region of its own.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import sddReminder from "./compaction-reminder.md" with { type: "text" };
import subagentReminder from "./compaction-reminder-subagent.md" with { type: "text" };

const SDD_MESSAGE = { customType: "post-compaction-reminder", content: sddReminder.trim(), display: true };
const SUBAGENT_REMINDER = subagentReminder.trim();
const SDD_PREFIX = "[sdd]";

type ModeChangeEntry = {
	type: "mode_change";
	mode: string;
	data?: { goal?: { objective?: string; status?: string } };
};
type SessionInitEntry = { type: "session_init"; task?: string };
type ShakeEndEvent = { action: string; aborted: boolean; skipped?: boolean; errorMessage?: string };
type CompactCtx = {
	agent: { kind: "main" | "sub" };
	sessionManager: { getBranch: () => Array<{ type: string }> };
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

/** The assignment: the `task` this session was prompted with, from its own `session_init`. */
function assignmentTask(ctx: CompactCtx): string | undefined {
	const branch = ctx.sessionManager.getBranch();
	for (let i = branch.length - 1; i >= 0; i--) {
		if (branch[i].type === "session_init") return (branch[i] as SessionInitEntry).task;
	}
	return undefined;
}

export default function (pi: ExtensionAPI) {
	let runContinues = false;
	let subagentReminded = false;

	const deliver = (message: typeof SDD_MESSAGE, ctx: CompactCtx) =>
		pi.sendMessage(message, { deliverAs: !ctx.isIdle() && runContinues ? "aside" : "nextTurn" });
	const remindSubagent = (ctx: CompactCtx) => {
		if (subagentReminded) return;
		subagentReminded = true;
		const task = assignmentTask(ctx);
		deliver(
			{
				customType: "post-compaction-subagent-reminder",
				content: task
					? `${SUBAGENT_REMINDER}\n\n<ORIGINAL_ASSIGNMENT>\n${task}\n</ORIGINAL_ASSIGNMENT>`
					: SUBAGENT_REMINDER,
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
	pi.on("session_compact", (_event: unknown, ctx: CompactCtx) => {
		if (ctx.agent.kind === "sub") remindSubagent(ctx);
		else if (sddGoalActive(ctx)) deliver(SDD_MESSAGE, ctx);
	});
	pi.on("auto_compaction_end", (event: ShakeEndEvent, ctx: CompactCtx) => {
		// A shake that fell through carries an errorMessage; the method after it commits and reminds.
		if (event.action !== "shake" || event.aborted || event.skipped || event.errorMessage !== undefined) return;
		if (ctx.agent.kind === "sub") remindSubagent(ctx);
	});
}
