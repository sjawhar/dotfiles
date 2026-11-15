// Three features of the experiments extension (index.ts) for the experiments of the eval method of
// dispatch://INFRA-3189. In the on arm, each one puts a fixed text into the context of a top-level
// session, or puts nothing there (aa_noop). In both arms, each one records the exposure of the
// session:
//
//   aa_noop             puts nothing in the context in either arm. It is the feature of the A/A
//                       test: it records its exposure at the point where the other two put their text.
//   infra805_skill      the following-best-practices skill of cybermonklabs/legion#2616, the first
//                       treatment of the experiment of dispatch://INFRA-805 (infra805-skill.md)
//   infra805_selfcheck  the one-paragraph self-ask instruction, the second treatment of that
//                       experiment (infra805-selfcheck.md)
//
// Where the texts come from. ./LICENSE.legion is a copy of the LICENSE file (Apache License 2.0) of
// cybermonklabs/legion.
//   - infra805-skill.md is a verbatim copy of skills/following-best-practices/SKILL.md of
//     cybermonklabs/legion at commit d806232f8b34deb96bd12b8fc2097d07479585f6 (git blob
//     cab9f7bf7937ded98538de09226e0aab7289e811). That commit is the head of #2616 that the dev-box
//     handover of 2026-11-12 names.
//   - infra805-selfcheck.md is one paragraph written for this arm, then three questions. The
//     questions are verbatim copies of the three questions of step 1 of that same file at that same
//     commit, d806232f8b34deb96bd12b8fc2097d07479585f6.
//
// The unit. A root session is a unit of an experiment when its file carries the `experiments` entry
// (index.ts) and at least one user prompt after that entry. A session that reaches that prompt gets
// an exposure entry, in either arm, or an `experiments-excluded` entry when it is outside the
// experiments (a Legion pane, below). A session with no such prompt gets neither. The join lists a
// session with no such prompt, and an excluded session, apart.
//
// Which sessions. index.ts runs these handlers while the gate of the feature is `on` or `random`, in
// both arms, and gives each handler the resolution of its feature (gates.ts). A gate that is `off`,
// in gates.json or by its variable, runs no handler: the feature is in no experiment, and the session
// gets no text and no entry. A handler that throws stops its own feature for the rest of the session.
// A Legion pane (LEGION_TREE, LEGION_ROLE or LEGION_CONTROLLER set, as proactive-memory-core.ts reads
// them) is outside the experiments. The INFRA-805 experiment of the spec of INFRA-3189 leaves Legion
// designs out until a Legion route exists. So in a Legion pane the handler adds no text and writes no
// exposure entry. At the trigger below it writes one `experiments-excluded` entry (ExcludedRecord of
// gates.ts: the feature, the reason `legion_pane`, the root session id and the time), and none again
// while one of this feature is on the branch. So the file shows why the session has no exposure.
//
// The trigger. The trigger is a user prompt of a top-level session (before_agent_start, not a
// revive). The context of the model is the part of the branch at or after its lower bound
// (contextLowerBound of skill-gate.ts: the entry after the latest reset, or the first kept entry of
// the latest compaction). At the trigger the handler makes two separate decisions:
//   - The text. In the on arm, the handler adds the text when the context holds no custom message
//     whose custom type is the name of the feature. So the text goes in at the first prompt, and
//     again after a reset or a compaction removes that message, also when the exposure entry stays.
//   - The entry. The handler writes an exposure entry when the context holds no exposure entry of
//     this feature in this arm. So the entry comes once for each arm while it stays in the context.
// A resumed session does not get either again while both stay in its context. A subagent never
// triggers. The unit of the experiments is the root session, and the skill tells its reader to
// start fresh subagents, so a subagent that reads it can start more.
//
// The text goes in as one hidden custom message (display false). Its custom type is the name of the
// feature, and omp sends it with the prompt. The model reads it, but the person at the session does
// not see the arm of the session. In the off arm, and for aa_noop, the handler adds nothing.
//
// Exposure. The handler appends one `experiments-exposure` entry (ExposureRecord of gates.ts: the
// feature, the arm, the gate, the root session id and the time) at the next request after the
// trigger (the `context` event). In the on arm of a feature with a text, the handler waits for the
// first request whose messages hold the text, so the entry says that the text reached the model.
//
// No text without its entry. When the append of the exposure entry fails, the handler logs the
// error, removes the text of its feature from the messages of that request, and stops: until the
// next resolution (index.ts), it adds no text, writes no entry, and removes its text from every
// request. The text stays in the session file, so a later resolution that finds the text in the
// context and no entry writes the entry at the next request that holds the text.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { EXCLUDED_ENTRY_TYPE, EXPOSURE_ENTRY_TYPE, type ExcludedRecord, type ExposureRecord, type Feature, type Resolution, rootSessionId } from "./gates";
import selfcheck from "./infra805-selfcheck.md" with { type: "text" };
import skill from "./infra805-skill.md" with { type: "text" };
import { LEGION_ENV } from "./proactive-memory-core";
import { contextLowerBound } from "./skill-gate";

type Entry = { type: string; id?: string; customType?: string; data?: unknown; [key: string]: unknown };
type Ctx = {
	agent: { kind: "main" | "sub" };
	sessionManager: { getBranch(): readonly Entry[]; getSessionFile(): string | undefined; getSessionId(): string };
};
type StartEvent = { reason?: "prompt" | "revive" };
type Message = { role: string; customType?: string };
type ContextEvent = { messages: Message[] };

/** A feature of this file: index.ts registers it with a view of `pi` and a reader of the resolution of the feature in the current session. */
export type TextArm = (pi: ExtensionAPI, resolution: () => Resolution) => void;

/** The feature that, in the on arm, puts `text` into the context of a top-level session, and in both arms records the exposure. With no text it puts nothing there in either arm. */
export function textArm(feature: Feature, text: string | undefined): TextArm {
	return (pi, resolution) => {
		if (LEGION_ENV.some(name => process.env[name] !== undefined && process.env[name] !== "")) {
			pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
				if (event.reason === "revive" || ctx.agent.kind !== "main") return undefined;
				const sm = ctx.sessionManager;
				const excluded = sm.getBranch().some(e => e.type === "custom" && e.customType === EXCLUDED_ENTRY_TYPE && (e.data as Partial<ExcludedRecord> | undefined)?.feature === feature);
				if (excluded) return undefined;
				const record: ExcludedRecord = { v: 1, feature, reason: "legion_pane", rootSessionId: rootSessionId(sm.getSessionFile(), sm.getSessionId()), at: new Date().toISOString() };
				pi.appendEntry(EXCLUDED_ENTRY_TYPE, record);
				return undefined;
			});
			return;
		}
		const isText = (m: Message) => m.role === "custom" && m.customType === feature;
		/** The result that removes the text of this feature from a request, or undefined when the request holds none. */
		const withoutText = (messages: Message[]) => (messages.some(isText) ? { messages: messages.filter(m => !isText(m)) } : undefined);
		// The arm and the gate of the latest trigger, until its exposure entry is written.
		let pending: Pick<ExposureRecord, "arm" | "gate"> | undefined;
		// Set when an append fails: no more text or entries, and the text is removed from each request.
		let withdrawn = false;
		const reset = () => {
			pending = undefined;
			withdrawn = false;
		};
		pi.on("session_start", reset);
		pi.on("session_switch", reset);
		pi.on("session_branch", reset);
		pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
			if (withdrawn || event.reason === "revive" || ctx.agent.kind !== "main") return undefined;
			const { gate, on } = resolution();
			if (gate === "off") return undefined;
			const arm = on ? "on" : "off";
			const branch = ctx.sessionManager.getBranch();
			const kept = branch.slice(contextLowerBound(branch));
			const exposed = kept.some(e => {
				const data = e.data as Partial<ExposureRecord> | undefined;
				return e.type === "custom" && e.customType === EXPOSURE_ENTRY_TYPE && data?.feature === feature && data.arm === arm;
			});
			if (!exposed) pending = { arm, gate };
			const textKept = kept.some(e => e.type === "custom_message" && e.customType === feature);
			return arm === "on" && text !== undefined && !textKept ? { message: { customType: feature, content: text, display: false } } : undefined;
		});
		pi.on("context", (event: ContextEvent, ctx: Ctx) => {
			if (withdrawn) return withoutText(event.messages);
			if (!pending) return undefined;
			if (pending.arm === "on" && text !== undefined && !event.messages.some(isText)) return undefined;
			try {
				const sm = ctx.sessionManager;
				const record: ExposureRecord = { v: 1, feature, ...pending, rootSessionId: rootSessionId(sm.getSessionFile(), sm.getSessionId()), at: new Date().toISOString() };
				pi.appendEntry(EXPOSURE_ENTRY_TYPE, record);
			} catch (error) {
				withdrawn = true;
				pi.logger.error(`experiments: ${feature} failed in context. It stays off for the rest of this session, and its text is removed from each request.`, {
					error: error instanceof Error ? (error.stack ?? error.message) : String(error),
				});
				return withoutText(event.messages);
			}
			pending = undefined;
			return undefined;
		});
	};
}

export const aaNoop = textArm("aa_noop", undefined);
export const infra805Skill = textArm("infra805_skill", skill);
export const infra805Selfcheck = textArm("infra805_selfcheck", selfcheck);
