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
// gets no new text and no entry. A handler that throws stops its own feature until the next
// resolution (index.ts): its handlers do not run, so it adds no text and writes no entry. For these
// features, and only these, index.ts reads gates.json again whenever the file changes on disk, at
// each before_agent_start and each context event, and draws their arm again from the same root id.
// So the resolution a handler reads is the one in force at that event. index.ts also counts each
// change of the state in force for these features (`generation`): each new resolution waiting to be
// recorded, and each recorded resolution, a gate going `off` and back included.
// A Legion pane (LEGION_TREE, LEGION_ROLE or LEGION_CONTROLLER set, as proactive-memory-core.ts reads
// them) is outside the experiments. The INFRA-805 experiment of the spec of INFRA-3189 leaves Legion
// designs out until a Legion route exists. So in a Legion pane the handler adds no text and writes no
// exposure entry. At a user prompt (below) it writes one `experiments-excluded` entry (ExcludedRecord
// of gates.ts: the feature, the reason `legion_pane`, the root session id and the time), and none
// again while one of this feature is on the branch. So the file shows why the session has no exposure.
//
// The user prompt. Architect ruling 48e7077f, item 2: a user prompt is a user-role message with no
// `agent` attribution that is not synthetic, or a user-invoked skill message (custom type
// `skill-prompt`, attribution `user`). omp 18.20.2 runs before_agent_start for a direct prompt
// (synthetic ones included), for a queued batch that holds user messages, and for a user-authored
// wake. Its event carries `reason` (`prompt` or `revive`), the prompt's text, its images and the
// system prompt. It carries no message, so no attribution and no synthetic flag, and the branch does
// not hold the new message yet. The first `context` event after it carries the messages of the
// request, with their roles, attribution, synthetic flags and custom types. So the handler decides
// at that first request: the run started at a user prompt when a message after the last assistant
// message is a user prompt (startsWithUserPrompt). That one test decides both the exposure entry and
// whether a text that the trigger added may go out.
//
// The trigger. The trigger is a before_agent_start of a top-level session that is not a revive. The
// context of the model is the part of the branch at or after its lower bound (contextLowerBound of
// skill-gate.ts: the entry after the latest reset, or the first kept entry of the latest compaction).
// At the trigger the handler makes two separate decisions:
//   - The text. In the on arm, the handler adds the text when the context holds no custom message
//     whose custom type is the name of the feature. So the text goes in at the first prompt, and
//     again after a reset or a compaction removes that message, also when the exposure entry stays.
//   - The entry. The handler owes an exposure entry when the context holds no exposure entry of this
//     feature in this arm. So the entry comes once for each arm while it stays in the context.
// At the first request after the trigger, the handler keeps the owed entry only when the run started
// at a user prompt. A text that the trigger added for a run that did not start at a user prompt is
// withheld: the scrub removes it until a later run starts at a user prompt.
// A resumed session does not get either again while both stay in its context. A subagent never
// triggers. The unit of the experiments is the root session, and the skill tells its reader to
// start fresh subagents, so a subagent that reads it can start more.
// Known limits: the text goes in only at a user prompt, so a compaction inside a turn removes it for
// the rest of that turn, and it comes back at the next user prompt. A side turn whose `context` event
// falls between a trigger and its first request would be read as that request [INFERENCE: omp starts
// the run at once after before_agent_start].
//
// The text goes in as one hidden custom message (display false). Its custom type is the name of the
// feature, and omp sends it with the prompt. The model reads it, but the person at the session does
// not see the arm of the session. In the off arm, and for aa_noop, the handler adds nothing.
//
// Exposure. The handler appends one `experiments-exposure` entry (ExposureRecord of gates.ts: the
// feature, the arm, the gate, the root session id and the time) at the first request after a trigger
// that started at a user prompt. In the on arm of a feature with a text, the handler waits for the
// first request whose messages hold the text, so the entry says that the text reached the model.
// When the state in force changed after the trigger (the count of index.ts moved, for example when a
// gate went `off` and back on), the trigger is stale. It writes no entry, a text it added is withheld,
// and the next user prompt triggers again under the state now in force. An owed entry that went stale
// before it was written is dropped the same way.
//
// The invariant: a text reaches the model only while the current arm of the session for its feature
// is on, and only with its entry. index.ts registers, for each feature of this file, the scrub of
// scrubText, after the handlers of every feature of this file. The scrub runs for every request,
// whatever the gate (`off` included) and whatever the failure state. It lets the text of the feature
// through only when all of these hold, and removes it otherwise:
//   - The session has a recorded resolution (index.ts), and the current arm of the feature is on.
//   - The feature did not stop after a throw, and the process is not a Legion pane.
//   - The text is not withheld (above).
//   - The context holds an exposure entry of the feature in the on arm, or the feature owes an entry
//     made under the state now in force, or this request's own exposure append succeeded. The last
//     needs no read of the branch.
// A throw of the permit (a branch that cannot be read) goes through the feature's logged failure path
// (index.ts): the feature stops until the next resolution, and the scrub removes the text.
// A text that an earlier resolution put in the session file stays there, but it reaches the model
// again only under these rules. So:
//   - When a gate goes from `random` to `off` (D13), a running session sends no text from its next
//     request on, because index.ts reads the changed gates.json at that request. A session resumed
//     later sends none either.
//   - No text goes out without its entry. When the exposure append (or any other handler of the
//     feature) throws, the feature stops, and the scrub removes the text from that request and from
//     every later one. A later resolution in the on arm that finds the text in the context and no
//     entry writes the entry at the next request after a user prompt that holds the text.
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
type Message = { role: string; customType?: string; attribution?: string; synthetic?: boolean };
type ContextEvent = { messages: Message[] };
/** The arm and the gate of a trigger. */
type Arm = Pick<ExposureRecord, "arm" | "gate">;

/** omp's custom type for a `/skill:` prompt (SKILL_PROMPT_MESSAGE_TYPE of @oh-my-pi/pi-tui). */
const SKILL_PROMPT = "skill-prompt";

/** Whether the input that started this run (the messages after the last assistant message) holds a user prompt, as architect ruling 48e7077f item 2 defines it. */
export function startsWithUserPrompt(messages: readonly Message[]): boolean {
	for (let i = messages.length - 1; i >= 0 && messages[i].role !== "assistant"; i--) {
		const m = messages[i];
		if (m.role === "user" && m.synthetic !== true && m.attribution !== "agent") return true;
		if (m.role === "custom" && m.customType === SKILL_PROMPT && m.attribution === "user") return true;
	}
	return false;
}

/** Whether the text of a feature of this file may go out in the session of `ctx`, apart from the checks of index.ts. */
export type Permit = (ctx: Ctx) => boolean;
/**
 * A feature of this file: index.ts registers it with a view of `pi`, a reader of the resolution of the feature in the
 * current session, and a reader of the count of changes of that state (index.ts `generation`), and gives its scrub the
 * permit it returns.
 */
export type TextArm = (pi: ExtensionAPI, resolution: () => Resolution, generation: () => number) => Permit;

/** Whether this process is a Legion pane, read as proactive-memory-core.ts reads it. */
const legionPane = () => LEGION_ENV.some(name => process.env[name] !== undefined && process.env[name] !== "");

/** The scrub of `feature`: a `context` handler that removes each custom message of `feature` from the request unless `permits(ctx)` holds. A throw of `permits` goes to `failed`, and the text is removed. */
export function scrubText(feature: Feature, permits: Permit, failed: (error: unknown) => void): (event: ContextEvent, ctx: Ctx) => { messages: Message[] } | undefined {
	const isText = (m: Message) => m.role === "custom" && m.customType === feature;
	return (event, ctx) => {
		if (!event.messages.some(isText)) return undefined;
		try {
			if (permits(ctx)) return undefined;
		} catch (error) {
			failed(error);
		}
		return { messages: event.messages.filter(m => !isText(m)) };
	};
}

/** The feature that, in the on arm, puts `text` into the context of a top-level session, and in both arms records the exposure. With no text it puts nothing there in either arm. */
export function textArm(feature: Feature, text: string | undefined): TextArm {
	return (pi, resolution, generation) => {
		const rootOf = (ctx: Ctx) => rootSessionId(ctx.sessionManager.getSessionFile(), ctx.sessionManager.getSessionId());
		if (legionPane()) {
			// Set by a trigger, until the first request after it.
			let triggered = false;
			const reset = () => {
				triggered = false;
			};
			pi.on("session_start", reset);
			pi.on("session_switch", reset);
			pi.on("session_branch", reset);
			pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
				if (event.reason !== "revive" && ctx.agent.kind === "main") triggered = true;
				return undefined;
			});
			pi.on("context", (event: ContextEvent, ctx: Ctx) => {
				if (!triggered) return undefined;
				triggered = false;
				if (!startsWithUserPrompt(event.messages)) return undefined;
				const excluded = ctx.sessionManager.getBranch().some(e => e.type === "custom" && e.customType === EXCLUDED_ENTRY_TYPE && (e.data as Partial<ExcludedRecord> | undefined)?.feature === feature);
				if (excluded) return undefined;
				const record: ExcludedRecord = { v: 1, feature, reason: "legion_pane", rootSessionId: rootOf(ctx), at: new Date().toISOString() };
				pi.appendEntry(EXCLUDED_ENTRY_TYPE, record);
				return undefined;
			});
			return () => false;
		}
		// The latest trigger, until the first request after it: its arm, the count it was made under, whether it owes an entry, and whether it added the text.
		let trigger: (Arm & { generation: number; owes: boolean; added: boolean }) | undefined;
		// The entry owed by a trigger that started at a user prompt, with the count it was made under, until it is written.
		let pending: (Arm & { generation: number }) | undefined;
		// Set when a trigger added the text for a run that did not start at a user prompt, or for a stale trigger, until a run that starts at a user prompt.
		let withheld = false;
		// Request-local: set when this request's exposure append succeeded, so this request's scrub needs no branch read.
		let recorded = false;
		const reset = () => {
			trigger = undefined;
			pending = undefined;
			withheld = false;
			recorded = false;
		};
		pi.on("session_start", reset);
		pi.on("session_switch", reset);
		pi.on("session_branch", reset);
		pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
			if (event.reason === "revive" || ctx.agent.kind !== "main") return undefined;
			const { gate, on } = resolution();
			if (gate === "off") return undefined;
			const arm = on ? "on" : "off";
			const branch = ctx.sessionManager.getBranch();
			const kept = branch.slice(contextLowerBound(branch));
			const exposed = kept.some(e => {
				const data = e.data as Partial<ExposureRecord> | undefined;
				return e.type === "custom" && e.customType === EXPOSURE_ENTRY_TYPE && data?.feature === feature && data.arm === arm;
			});
			const add = arm === "on" && text !== undefined && !kept.some(e => e.type === "custom_message" && e.customType === feature);
			trigger = { arm, gate, generation: generation(), owes: !exposed, added: add };
			return add ? { message: { customType: feature, content: text, display: false } } : undefined;
		});
		pi.on("context", (event: ContextEvent, ctx: Ctx) => {
			recorded = false;
			if (trigger) {
				const t = trigger;
				trigger = undefined;
				if (t.generation === generation() && startsWithUserPrompt(event.messages)) {
					withheld = false;
					if (t.owes) pending = { arm: t.arm, gate: t.gate, generation: t.generation };
				} else if (t.added) withheld = true;
			}
			if (!pending) return undefined;
			if (pending.generation !== generation()) {
				pending = undefined;
				return undefined;
			}
			if (pending.arm === "on" && text !== undefined && !event.messages.some(m => m.role === "custom" && m.customType === feature)) return undefined;
			const record: ExposureRecord = { v: 1, feature, arm: pending.arm, gate: pending.gate, rootSessionId: rootOf(ctx), at: new Date().toISOString() };
			// A throw here stops the feature (index.ts), and the scrub then removes its text from this request.
			pi.appendEntry(EXPOSURE_ENTRY_TYPE, record);
			pending = undefined;
			recorded = true;
			return undefined;
		});
		return ctx => {
			if (withheld) return false;
			if (recorded) return true;
			if (pending?.arm === "on" && pending.generation === generation()) return true;
			const branch = ctx.sessionManager.getBranch();
			for (let i = branch.length - 1, low = contextLowerBound(branch); i >= low; i--) {
				const e = branch[i];
				const data = e.data as Partial<ExposureRecord> | undefined;
				if (e.type === "custom" && e.customType === EXPOSURE_ENTRY_TYPE && data?.feature === feature && data.arm === "on") return true;
			}
			return false;
		};
	};
}

export const aaNoop = textArm("aa_noop", undefined);
export const infra805Skill = textArm("infra805_skill", skill);
export const infra805Selfcheck = textArm("infra805_selfcheck", selfcheck);
