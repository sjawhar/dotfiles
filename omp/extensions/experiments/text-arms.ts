// Three features of the experiments extension (index.ts) for the experiments of the eval method of
// dispatch://INFRA-3189. In the on arm, each one puts a fixed text into the context of a top-level
// session, or puts nothing there (aa_noop). In both arms, each one records the exposure of the
// session, so the join finds an exposure entry for every assigned session:
//
//   aa_noop             puts nothing in the context in either arm. It is the feature of the A/A
//                       test: it records its exposure at the point where the other two put their text.
//   infra805_skill      the following-best-practices skill of cybermonklabs/legion#2616, the first
//                       treatment of the experiment of dispatch://INFRA-805 (infra805-skill.md)
//   infra805_selfcheck  the one-paragraph self-ask instruction, the second treatment of that
//                       experiment (infra805-selfcheck.md)
//
// Where the texts come from. Both are verbatim copies from cybermonklabs/legion, under the Apache
// License 2.0. ./LICENSE.legion is a copy of the LICENSE file of that repository.
//   - infra805-skill.md is skills/following-best-practices/SKILL.md at commit
//     d806232f8b34deb96bd12b8fc2097d07479585f6 (git blob cab9f7bf7937ded98538de09226e0aab7289e811).
//     That commit is the head of #2616 that the dev-box handover of 2026-11-12 names.
//   - infra805-selfcheck.md is lines 12 to 16 of skills/following-best-practices/SKILL.md at commit
//     985d8fabea609510f68fabdb2b96f0c312cd20d2: the paragraph and the three questions of its step 1.
//     dispatch://INFRA-805/artifact/tests-md (version 1 of 2026-11-01T02:18:42Z, also versions 2 and
//     3) describes this arm as "a one-paragraph instruction to answer the same three questions
//     oneself (the form step 1 now has)". That document holds no text of the paragraph. Commit
//     985d8fabe has the author time 2026-11-01T02:16:16Z, two minutes before tests-md version 1. It
//     is the step 1 that the sentence names.
//
// Which sessions. index.ts runs these handlers while the gate of the feature is `on` or `random`, in
// both arms, and gives each handler the resolution of its feature (gates.ts). A gate that is `off`,
// in gates.json or by its variable, runs no handler: the feature is in no experiment, and the session
// gets no text and no entry. A handler that throws stops its own feature for the rest of the session.
//
// The trigger. The trigger is a prompt of a top-level session (before_agent_start, not a revive) when
// the context of the model holds no exposure entry of this feature and arm. An entry is in the context
// when it is on the branch at or after the lower bound of the context (contextLowerBound of
// skill-gate.ts: the entry after the latest reset, or the first kept entry of the latest compaction).
// So the trigger comes at the first prompt of a session, and again after a reset or a compaction
// removes the entry. A resumed session does not trigger again. A subagent never triggers. The unit
// of the experiments is the root session, and the skill tells its reader to start fresh subagents,
// so a subagent that reads it can start more.
//
// The text. In the on arm, the trigger adds the text as one hidden custom message. The custom type of
// that message is the name of the feature, and omp sends the message with the prompt. The message is
// hidden (display false): the model reads it, but the person at the session does not see the arm of
// the session. In the off arm, and for aa_noop, the trigger adds nothing.
//
// Exposure. The handler appends one `experiments-exposure` entry (ExposureRecord of gates.ts: the
// feature, the arm, the gate, the root session id and the time) at the next request after the
// trigger (the `context` event). In the on arm of a feature with a text, the handler waits for the
// first request whose messages hold the text, so the entry says that the text reached the model.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { EXPOSURE_ENTRY_TYPE, type ExposureRecord, type Feature, type Resolution, rootSessionId } from "./gates";
import selfcheck from "./infra805-selfcheck.md" with { type: "text" };
import skill from "./infra805-skill.md" with { type: "text" };
import { contextLowerBound } from "./skill-gate";

type Entry = { type: string; id?: string; customType?: string; data?: unknown; [key: string]: unknown };
type Ctx = {
	agent: { kind: "main" | "sub" };
	sessionManager: { getBranch(): readonly Entry[]; getSessionFile(): string | undefined; getSessionId(): string };
};
type StartEvent = { reason?: "prompt" | "revive" };
type ContextEvent = { messages: ReadonlyArray<{ role: string; customType?: string }> };

/** Whether the branch holds an exposure entry of `feature` in `arm` that the context of the model still covers. */
export function exposedInContext(branch: readonly Entry[], feature: Feature, arm: ExposureRecord["arm"]): boolean {
	for (let i = contextLowerBound(branch); i < branch.length; i++) {
		const entry = branch[i];
		if (entry.type !== "custom" || entry.customType !== EXPOSURE_ENTRY_TYPE) continue;
		const data = entry.data as Partial<ExposureRecord> | undefined;
		if (data?.feature === feature && data.arm === arm) return true;
	}
	return false;
}

/** A feature of this file: index.ts registers it with a view of `pi` and a reader of the resolution of the feature in the current session. */
export type TextArm = (pi: ExtensionAPI, resolution: () => Resolution) => void;

/** The feature that, in the on arm, puts `text` into the context of a top-level session, and in both arms records the exposure. With no text it puts nothing there in either arm. */
export function textArm(feature: Feature, text: string | undefined): TextArm {
	return (pi, resolution) => {
		// The arm and the gate of the latest trigger, until its exposure entry is written.
		let pending: Pick<ExposureRecord, "arm" | "gate"> | undefined;
		const reset = () => {
			pending = undefined;
		};
		pi.on("session_start", reset);
		pi.on("session_switch", reset);
		pi.on("session_branch", reset);
		pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
			if (event.reason === "revive" || ctx.agent.kind !== "main") return undefined;
			const { gate, on } = resolution();
			if (gate === "off") return undefined;
			const arm = on ? "on" : "off";
			if (exposedInContext(ctx.sessionManager.getBranch(), feature, arm)) return undefined;
			pending = { arm, gate };
			return arm === "on" && text !== undefined ? { message: { customType: feature, content: text, display: false } } : undefined;
		});
		pi.on("context", (event: ContextEvent, ctx: Ctx) => {
			if (!pending) return undefined;
			if (pending.arm === "on" && text !== undefined && !event.messages.some(m => m.role === "custom" && m.customType === feature)) return undefined;
			const sm = ctx.sessionManager;
			const record: ExposureRecord = { v: 1, feature, ...pending, rootSessionId: rootSessionId(sm.getSessionFile(), sm.getSessionId()), at: new Date().toISOString() };
			pending = undefined;
			pi.appendEntry(EXPOSURE_ENTRY_TYPE, record);
			return undefined;
		});
	};
}

export const aaNoop = textArm("aa_noop", undefined);
export const infra805Skill = textArm("infra805_skill", skill);
export const infra805Selfcheck = textArm("infra805_selfcheck", selfcheck);
