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
// `skill-prompt`, attribution `user`). The omp the dev box pins (18.18.6-sami.20261110-032522, in
// mise.toml) runs before_agent_start for a direct prompt (synthetic ones included), for a queued
// batch that holds user messages, and for a user-authored wake. Its event carries `reason` (`prompt`
// or `revive`), the prompt's text, its images and the system prompt. It carries no message, so no
// attribution and no synthetic flag, and the branch does not hold the new message yet. So the handler
// decides at the run's first request, which carries the run's input with its roles, attribution,
// synthetic flags and custom types. The run started at a user prompt when that input holds one. That
// one test decides both the exposure entry and whether a text that the trigger added may go out.
//
// The run's first request. omp can send other requests between before_agent_start and the run. A
// pre-prompt compaction, or a background one, sends a part of the session file to its summarizer. omp
// can cut that part inside a turn, so it can end in an assistant message, a tool result or an older
// prompt. So the handler does not read the shape of a request. At the trigger it notes the time, and
// the role and the time of each message on the branch (snapshot). The input of the run in a request is
// each user, developer or custom message that was made no later than the trigger and was not on the
// branch then (runInput). A compaction's request holds only stored messages. A message that a
// `context` handler adds, such as proactive memory's reminder, is made after the trigger. So the first
// request that holds such input is the run's first request. A queued follow-up counts too, although
// it is older than the replies that omp stored after it. Until that request the handler decides
// nothing and writes nothing.
//
// The trigger. The trigger is a before_agent_start of a top-level session that is not a revive. The
// context of the model is the part of the branch at or after its lower bound (contextLowerBound of
// skill-gate.ts: the entry after the latest reset, or the first kept entry of the latest compaction).
// At the trigger the handler makes two separate decisions:
//   - The text. In the on arm, the handler adds the text when no custom message of the feature in
//     the context holds the text byte for byte. So the text goes in at the first prompt, and again
//     after a reset or a compaction removes that message, also when the exposure entry stays. omp's
//     shake can splice a placeholder (`[shaken ~N tokens …]`) into a kept custom message. Such an
//     altered copy does not count, so the next user prompt puts the whole text in again.
//   - The entry. The handler owes an exposure entry when the context holds no exposure entry of this
//     feature in this arm. So the entry comes once for each arm while it stays in the context.
// At the run's first request, the handler writes the owed entry only when the run started at a user
// prompt. A text that the trigger added for a run that did not start at a user prompt is withheld: the
// scrub removes it until a later run starts at a user prompt.
// A resumed session does not get either again while both stay in its context. A subagent never
// triggers. The unit of the experiments is the root session, and the skill tells its reader to
// start fresh subagents, so a subagent that reads it can start more.
// Known limits: the text goes in only at a user prompt. A compaction at the start of a turn (the
// pre-prompt compaction above) or inside it can remove the text, and an omp shake can alter it. The
// scrub then keeps the altered copy out, and the whole text comes back at the next user prompt. A side
// turn (`/btw`) whose message was made before the trigger, and whose request falls between the
// trigger and the run's first request, would be read as that request [INFERENCE: rare. That side turn
// is not a user prompt, so the trigger writes no entry, and a text it added waits for the next user
// prompt].
//
// The text goes in as one hidden custom message (display false). Its custom type is the name of the
// feature, and omp sends it with the prompt. The model reads it, but the person at the session does
// not see the arm of the session. In the off arm, and for aa_noop, the handler adds nothing.
//
// Exposure. The handler appends one `experiments-exposure` entry (ExposureRecord of gates.ts: the
// feature, the arm, the gate, the root session id and the time) at the run's first request, when the
// run started at a user prompt and the trigger owes the entry. In the on arm of a feature with a text,
// that request must hold the whole text, so the entry says that the text reached the model. A run
// whose first request does not hold it (a pre-prompt compaction removed a kept copy) writes no entry,
// and no later request of that run writes one. The next user prompt then puts the text in again and
// owes the entry again. When the state in force changed after the trigger (the count of index.ts
// moved, for example when a gate went `off` and back on), the trigger is stale. It writes no entry, a
// text it added is withheld, and the next user prompt triggers again under the state now in force.
//
// The invariant: a text reaches the model only while the current arm of the session for its feature
// is on, and only with its entry. index.ts registers, for each feature of this file, the scrub of
// scrubText, after the handlers of every feature of this file. The scrub runs for every request,
// whatever the gate (`off` included) and whatever the failure state. It lets the text of the feature
// through only when all of these hold, and removes it otherwise:
//   - The session has a recorded resolution (index.ts), and the current arm of the feature is on.
//   - The feature did not stop after a throw, and the process is not a Legion pane.
//   - The text is not withheld (above).
//   - The context holds an exposure entry of the feature in the on arm, or this request's own exposure
//     append succeeded. The second needs no read of the branch.
// The handler writes an entry only at the first request of a run that started at a user prompt, and
// that run clears the withholding. So no entry is written for a request whose text the scrub then
// removes.
// A throw of the permit (a branch that cannot be read) goes through the feature's logged failure path
// (index.ts): the feature stops until the next resolution, and the scrub removes the text.
// The scrub always removes a custom message of the feature whose content is not the text byte for
// byte (a copy that omp's shake altered), so an altered treatment never reaches the model.
// A text that an earlier resolution put in the session file stays there, but it reaches the model
// again only under these rules. So:
//   - When a gate goes from `random` to `off` (D13), a running session sends no text from its next
//     request on, because index.ts reads the changed gates.json at that request. A session resumed
//     later sends none either.
//   - No text goes out without its entry. When the exposure append (or any other handler of the
//     feature) throws, the feature stops, and the scrub removes the text from that request and from
//     every later one. A later resolution in the on arm that finds the text in the context and no
//     entry writes the entry at the first request of the next run that starts at a user prompt.
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
type Message = { role: string; customType?: string; content?: unknown; attribution?: string; synthetic?: boolean; timestamp?: unknown };
type ContextEvent = { messages: Message[] };
/** The arm and the gate of a trigger. */
type Arm = Pick<ExposureRecord, "arm" | "gate">;

/** omp's custom type for a `/skill:` prompt (SKILL_PROMPT_MESSAGE_TYPE of @oh-my-pi/pi-tui). */
const SKILL_PROMPT = "skill-prompt";

/** Whether `m` is a user prompt, as architect ruling 48e7077f item 2 defines it. */
function isUserPrompt(m: Message): boolean {
	if (m.role === "user") return m.synthetic !== true && m.attribution !== "agent";
	return m.role === "custom" && m.customType === SKILL_PROMPT && m.attribution === "user";
}

/** The roles of the messages that can start a run: a prompt, a synthetic prompt, a queued user message, a skill prompt or a wake. */
const INPUT_ROLES: Record<string, true> = { user: true, developer: true, custom: true };
/** A message's key: its role and its timestamp, as a request carries them. omp gives a stored custom message the epoch milliseconds of its entry's ISO time (getMessageFromEntry, customMessageEntryMessage). */
const keyOf = (role: string, timestamp: unknown) => `${role}:${String(timestamp)}`;

/** What a trigger saw: the keys of the messages on the branch, and the time. */
type Snapshot = { stored: ReadonlySet<string>; at: number };
function snapshot(branch: readonly Entry[]): Snapshot {
	const stored = new Set<string>();
	for (const e of branch) {
		if (e.type === "message") {
			const m = e.message as { role?: unknown; timestamp?: unknown } | undefined;
			if (typeof m?.role === "string") stored.add(keyOf(m.role, m.timestamp));
		} else if (e.type === "custom_message" && typeof e.timestamp === "string") stored.add(keyOf("custom", new Date(e.timestamp).getTime()));
	}
	return { stored, at: Date.now() };
}

/**
 * The input of the run of a trigger in a request: its messages of an input role that were made no later than the trigger
 * and were not on the branch then. Only the run's first request holds one: a compaction's request holds only stored
 * messages, and a message that a `context` handler adds is made after the trigger.
 */
const runInput = (messages: readonly Message[], seen: Snapshot) =>
	messages.filter(m => INPUT_ROLES[m.role] === true && typeof m.timestamp === "number" && m.timestamp <= seen.at && !seen.stored.has(keyOf(m.role, m.timestamp)));

/** Whether the text of a feature of this file may go out in the session of `ctx`, apart from the checks of index.ts. */
export type Permit = (ctx: Ctx) => boolean;
/** What a registered feature of this file gives index.ts for its scrub: its permit, and its text (undefined for aa_noop). */
export type TextArmHandle = { permit: Permit; text: string | undefined };
/**
 * A feature of this file: index.ts registers it with a view of `pi`, a reader of the resolution of the feature in the
 * current session, and a reader of the count of changes of that state (index.ts `generation`), and builds its scrub
 * from the handle it returns.
 */
export type TextArm = (pi: ExtensionAPI, resolution: () => Resolution, generation: () => number) => TextArmHandle;

/** Whether this process is a Legion pane, read as proactive-memory-core.ts reads it. */
const legionPane = () => LEGION_ENV.some(name => process.env[name] !== undefined && process.env[name] !== "");

/**
 * The scrub of `feature`: a `context` handler that removes each custom message of `feature` from the request, except a
 * copy that holds `text` byte for byte while `permits(ctx)` holds. So an altered copy (omp's shake) never goes out.
 * `permits` is read only when the request holds a whole copy. A throw of it goes to `failed`, and the copies are removed.
 */
export function scrubText(feature: Feature, text: string | undefined, permits: Permit, failed: (error: unknown) => void): (event: ContextEvent, ctx: Ctx) => { messages: Message[] } | undefined {
	const ofFeature = (m: Message) => m.role === "custom" && m.customType === feature;
	return (event, ctx) => {
		if (!event.messages.some(ofFeature)) return undefined;
		let permitted = false;
		if (text !== undefined && event.messages.some(m => ofFeature(m) && m.content === text)) {
			try {
				permitted = permits(ctx);
			} catch (error) {
				failed(error);
			}
		}
		const keep = (m: Message) => !ofFeature(m) || (permitted && m.content === text);
		return event.messages.every(keep) ? undefined : { messages: event.messages.filter(keep) };
	};
}

/** The feature that, in the on arm, puts `text` into the context of a top-level session, and in both arms records the exposure. With no text it puts nothing there in either arm. */
export function textArm(feature: Feature, text: string | undefined): TextArm {
	return (pi, resolution, generation) => {
		const rootOf = (ctx: Ctx) => rootSessionId(ctx.sessionManager.getSessionFile(), ctx.sessionManager.getSessionId());
		if (legionPane()) {
			// What the latest trigger saw, until its run's first request.
			let trigger: Snapshot | undefined;
			const reset = () => {
				trigger = undefined;
			};
			pi.on("session_start", reset);
			pi.on("session_switch", reset);
			pi.on("session_branch", reset);
			pi.on("before_agent_start", (event: StartEvent, ctx: Ctx) => {
				if (event.reason !== "revive" && ctx.agent.kind === "main") trigger = snapshot(ctx.sessionManager.getBranch());
				return undefined;
			});
			pi.on("context", (event: ContextEvent, ctx: Ctx) => {
				if (!trigger) return undefined;
				const input = runInput(event.messages, trigger);
				if (input.length === 0) return undefined;
				trigger = undefined;
				if (!input.some(isUserPrompt)) return undefined;
				const excluded = ctx.sessionManager.getBranch().some(e => e.type === "custom" && e.customType === EXCLUDED_ENTRY_TYPE && (e.data as Partial<ExcludedRecord> | undefined)?.feature === feature);
				if (excluded) return undefined;
				const record: ExcludedRecord = { v: 1, feature, reason: "legion_pane", rootSessionId: rootOf(ctx), at: new Date().toISOString() };
				pi.appendEntry(EXCLUDED_ENTRY_TYPE, record);
				return undefined;
			});
			return { permit: () => false, text };
		}
		// The latest trigger, until its run's first request: its arm, the count it was made under, whether it owes an entry, whether it added the text, and what it saw.
		let trigger: (Arm & Snapshot & { generation: number; owes: boolean; added: boolean }) | undefined;
		// Set when a trigger added the text for a run that did not start at a user prompt, or for a stale trigger, until a run that starts at a user prompt.
		let withheld = false;
		// Request-local: set when this request's exposure append succeeded, so this request's scrub needs no branch read.
		let recorded = false;
		const reset = () => {
			trigger = undefined;
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
			const add = arm === "on" && text !== undefined && !kept.some(e => e.type === "custom_message" && e.customType === feature && e.content === text);
			trigger = { arm, gate, generation: generation(), owes: !exposed, added: add, ...snapshot(branch) };
			return add ? { message: { customType: feature, content: text, display: false } } : undefined;
		});
		pi.on("context", (event: ContextEvent, ctx: Ctx) => {
			recorded = false;
			if (!trigger) return undefined;
			// A request with no input of the run (a compaction's): the trigger waits for the run's first request.
			const input = runInput(event.messages, trigger);
			if (input.length === 0) return undefined;
			const t = trigger;
			trigger = undefined;
			if (t.generation !== generation() || !input.some(isUserPrompt)) {
				if (t.added) withheld = true;
				return undefined;
			}
			withheld = false;
			if (!t.owes) return undefined;
			if (t.arm === "on" && text !== undefined && !event.messages.some(m => m.role === "custom" && m.customType === feature && m.content === text)) return undefined;
			const record: ExposureRecord = { v: 1, feature, arm: t.arm, gate: t.gate, rootSessionId: rootOf(ctx), at: new Date().toISOString() };
			// A throw here stops the feature (index.ts), and the scrub then removes its text from this request.
			pi.appendEntry(EXPOSURE_ENTRY_TYPE, record);
			recorded = true;
			return undefined;
		});
		// Every condition here lets out a text that has its entry: one this request wrote, or one in the context.
		const permit: Permit = ctx => {
			if (withheld) return false;
			if (recorded) return true;
			const branch = ctx.sessionManager.getBranch();
			for (let i = branch.length - 1, low = contextLowerBound(branch); i >= low; i--) {
				const e = branch[i];
				const data = e.data as Partial<ExposureRecord> | undefined;
				if (e.type === "custom" && e.customType === EXPOSURE_ENTRY_TYPE && data?.feature === feature && data.arm === "on") return true;
			}
			return false;
		};
		return { permit, text };
	};
}

export const aaNoop = textArm("aa_noop", undefined);
export const infra805Skill = textArm("infra805_skill", skill);
export const infra805Selfcheck = textArm("infra805_selfcheck", selfcheck);
