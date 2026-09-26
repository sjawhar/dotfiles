// Re-assert the sdd process contract across compaction, only while an sdd goal
// is active.
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
// remote, snapcompact, handoff, shake, soft — once the summary has replaced
// the history. The reminder is a persisted, displayed custom message either
// way; only the queue differs, because omp has no single delivery that fits
// every moment a compaction can land:
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
// handler also runs for their compactions. Only the top-level session is the
// coordinator following the process, so subagent transcripts (which live
// inside the parent's session directory as <AgentName>.jsonl rather than a
// <timestamp>_<uuid>.jsonl) are skipped; the grammar is the one session-env.ts
// uses for the same distinction. State lives in the factory closure, which is
// per session binding, never at module scope shared across sessions.
import path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import reminder from "./compaction-reminder.md" with { type: "text" };

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const TOP_LEVEL_TRANSCRIPT = new RegExp(`^\\d{4}-\\d{2}-\\d{2}T[\\d-]+Z_${UUID}\\.jsonl$`);
const MESSAGE = { customType: "post-compaction-reminder", content: reminder.trim(), display: true };
const SDD_PREFIX = "[sdd]";

type ModeChangeEntry = {
	type: "mode_change";
	mode: string;
	data?: { goal?: { objective?: string; status?: string } };
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

export default function (pi: ExtensionAPI) {
	let runContinues = false;

	pi.on("turn_end", (event: { toolResults: unknown[] }) => {
		runContinues = event.toolResults.length > 0;
	});
	pi.on("agent_end", () => {
		runContinues = false;
	});
	pi.on("session_compact", (_event: unknown, ctx: CompactCtx) => {
		const file = ctx.sessionManager.getSessionFile();
		if (!file || !TOP_LEVEL_TRANSCRIPT.test(path.basename(file))) return;
		if (!sddGoalActive(ctx)) return;
		pi.sendMessage(MESSAGE, { deliverAs: !ctx.isIdle() && runContinues ? "aside" : "nextTurn" });
	});
}
