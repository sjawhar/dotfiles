// Three features of the experiments extension (index.ts) for the experiments of the eval method of
// dispatch://INFRA-3189. In the on arm, each of the two INFRA-805 features puts a fixed text into
// every request of the session. In both arms, each of the three records the exposure of the session:
//
//   aa_noop             changes no request in either arm. It is the feature of the A/A test: it
//                       records its exposure where the other two put their text in.
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
// The treatment (architect ruling 8, dispatch://INFRA-3189/comment/a84aca43-ee6b-4d66-b2f2-5fad6bf08bef).
// While the current arm of a feature with a text is on, every request of the session carries the
// whole text once, at one fixed early position. The text is never stored in the session, so no
// compaction can remove it and no shake can alter it. This holds in the root session and in each of
// its subagents, which draw from the root's id (rootIdFromPath of gates.ts), whatever starts the run:
// a user prompt, a tool turn, a wake. So the treatment of a unit is the text in every request after
// its assignment. When the arm is not on (a gate `off`, a draw `off`, a Legion pane, a feature that
// stopped after a throw, a resolution not yet recorded), no request carries the text.
//
// How. placeTexts is the last `context` handler of index.ts. It runs outside the gate and the failure
// guard, so it sees every request whatever the gate and the failure state. It takes out of the
// request every custom message whose custom type is `infra805_skill` or `infra805_selfcheck`, whatever
// its content (a copy that an earlier version of this file stored, or one that omp's shake altered).
// Then, for each of the two whose arm is on, it puts in one hidden custom message with the whole
// text: the skill first, then the self-check. omp converts that message to a developer message
// attributed to the user, which its Anthropic converter sends as a `user` turn and never makes a
// system message. Its time is fixed at 0, so the message is the same in every request. omp keys a
// turn by its role, time and attribution (date-cwd-reminder.ts), and a time of 0 never makes the
// Anthropic converter put a compaction's file list ahead of it (flushCompactionFiles). The superpowers
// patch in omp/plugins/patches fixes its injected message's time for the same reason. scrubPayload,
// the last `before_provider_request` handler of index.ts, also runs outside the gate and the guard:
// it takes the text of each feature whose arm is not on out of the provider's payload. So a request
// whose feature stopped after placeTexts ran (Exposure, below), and a cache-warming replay of an
// older request, send no text whose arm is not on.
//
// The position. Before the first message of the request, unless the request opens with omp's native
// Anthropic compaction summary. The API drops every block before a compaction block, and omp folds a
// directly following assistant turn into that block. So the texts then go after the summary, that
// assistant turn and its tool results. That is omp's own rule for its context notes
// (buildSessionContext in session-context.ts). The position depends only on how the history opens,
// so it is the same in every request until the next compaction, and the prompt cache keeps the
// prefix through the texts.
//
// Exposure. recordExposure registers two handlers for each of the three. index.ts runs them in both
// arms of a gate that is `on` or `random`, and only under a recorded resolution (an `experiments`
// entry, index.ts). The `context` handler owes the entry when the session file holds none of this
// feature, arm and gate after its latest `experiments` entry (a resumed session holds one). It
// writes nothing, because some `context` events send nothing: `/dump`, and the first of the two
// conversions of buildLiveProviderContext (agent-session.ts). The `before_provider_request` handler
// writes the owed entry. omp emits that event only as a provider sends a request: its payload, the
// provider's own request body, is all it carries besides the model. So the first provider request
// after each recorded resolution gets one `experiments-exposure` entry (ExposureRecord of gates.ts:
// the feature, the arm, the gate, the root session id and the time), and a subagent's file gets its
// own. Some provider requests go past the `context` handlers and carry no text: an advisor's, omp's
// local summarizer's, a cache-warming replay. In the on arm of a feature with a text, the entry is
// written only for a request whose payload carries that text, so it says that the text reached the
// model. An append that throws stops the feature until the next resolution (index.ts). scrubPayload,
// the last `before_provider_request` handler, then takes the text out of that request's payload, and
// placeTexts gives no later request the text. A gate `off` runs none of it and writes nothing.
// Which assigned sessions are units is the join's to decide: a unit is a root session whose file
// carries the `experiments` entry and a user prompt after it (architect ruling 48e7077f, item 2),
// and the join reads the prompts from the session file. This file detects no prompt.
//
// A Legion pane (LEGION_TREE, LEGION_ROLE or LEGION_CONTROLLER set, as proactive-memory-core.ts reads
// them) is outside the experiments. The INFRA-805 experiment of the spec of INFRA-3189 leaves Legion
// designs out until a Legion route exists. So a Legion pane gets no text and no exposure entry. At its
// first provider request under a gate `on` or `random`, each of the three appends one
// `experiments-excluded` entry (ExcludedRecord of gates.ts: the feature, the reason `legion_pane`, the
// root session id and the time), unless the session file already holds one of the feature. So the
// file shows why the session has no exposure.
//
// Limits, read in omp at the version mise.toml pins (18.18.6-sami.20261110-032522):
//   - A compaction's summarizer. omp's native Anthropic compaction builds its request through the
//     `context` handlers (buildLiveProviderContext in agent-session.ts), so the summarizer reads the
//     texts where the live requests carried them. That is wanted: omp needs the summarized part to
//     match what the live turns sent, for their signed thinking and for the prompt cache. But the
//     summary can restate parts of a text, and the summary is stored, so after an `off` push a session
//     can keep such parts. omp's local summarizer, and the advisors' side requests, convert the history
//     without the `context` handlers (convertToLlmForSideRequest), so they never read a text.
//   - The prompt cache. omp marks a message that a `context` handler adds as made for one call
//     (markPerCallContextMessage in emitContext of extensions/runner.ts). Its Anthropic provider
//     (applyPromptCaching in anthropic.ts) still puts its two rolling breakpoints on the last two
//     messages that are not such messages, so each request reads the prefix, the texts included,
//     from the cache. What the on arm loses: the checkpoint every 15 conversational user turns, which
//     counts only the turns before the first such message (the texts are at the front, so it counts
//     none), and the anchor on the message just before it. Without them, a request whose new tail is
//     too far from the last cached breakpoint writes the cache again. A request with such a message
//     also goes without a message thread, which changes no token use or price (anthropic-threads.ts),
//     only the bytes uploaded and the time to the first token. [INFERENCE] When another `context`
//     handler already puts such a message at the front in both arms (the superpowers bootstrap, if
//     that plugin is loaded), both arms lose the same. The A/A cannot show the cost: aa_noop adds no
//     message. So the registration reads cache-write and cache-read input tokens per arm.
//   - A text that starts or stops in a running session (a gate change) changes the prefix of the turns
//     before it. On a model that binds its thinking to the prefix, omp then drops that thinking (its
//     prefix-mismatch handling in anthropic.ts).
//   - placeTexts and scrubPayload take out the two features' own messages only. A text that reaches
//     the session another way (a tool that reads the file, a summary that restates it) stays.
//   - The owed entry is the next provider request's. After a `/dump`, a request that went past the
//     `context` handlers (above) before the next conversion takes it: in the off arm, and for
//     aa_noop, whatever it carries; in the on arm, only when it carries the text (a cache-warming
//     replay of a request that did). omp sends the request of an automatic native compaction with no
//     `before_provider_request`, so its text goes out and the next request writes the entry.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { ENTRY_TYPE, EXCLUDED_ENTRY_TYPE, EXPOSURE_ENTRY_TYPE, type ExcludedRecord, type ExperimentRecord, type ExposureRecord, type Feature, rootSessionId } from "./gates";
import selfcheck from "./infra805-selfcheck.md" with { type: "text" };
import skill from "./infra805-skill.md" with { type: "text" };
import { LEGION_ENV } from "./proactive-memory-core";

type Entry = { type: string; customType?: string; data?: unknown };
type Ctx = { sessionManager: { getEntries(): readonly Entry[]; getSessionFile(): string | undefined; getSessionId(): string } };
type Message = { role: string; customType?: string; providerPayload?: { type?: string }; [key: string]: unknown };
type ContextEvent = { messages: Message[] };
/** omp's `before_provider_request`: the provider's request body, as it is about to go out. */
type ProviderRequestEvent = { payload: unknown };

/** The features of this file and their texts, in the order the texts go into a request. aa_noop has none. */
export const TEXT_ARMS: ReadonlyArray<readonly [Feature, string | undefined]> = [
	["aa_noop", undefined],
	["infra805_skill", skill],
	["infra805_selfcheck", selfcheck],
];
const TEXTS = TEXT_ARMS.filter((arm): arm is readonly [Feature, string] => arm[1] !== undefined);
const TEXT_TYPES: ReadonlySet<string> = new Set(TEXTS.map(([feature]) => feature));

/** Whether this process is a Legion pane, read as proactive-memory-core.ts reads it. */
const legionPane = () => LEGION_ENV.some(name => process.env[name] !== undefined && process.env[name] !== "");

/**
 * Where the texts go: before the first message, or, when the request opens with omp's native Anthropic compaction
 * summary, after it, a directly following assistant turn and that turn's tool results (omp's rule for its context
 * notes, buildSessionContext in session-context.ts).
 */
function earlyPosition(messages: readonly Message[]): number {
	const head = messages[0];
	if (head?.role !== "compactionSummary" || head.providerPayload?.type !== "anthropicCompaction") return 0;
	let at = 1;
	if (messages[at]?.role === "assistant") at++;
	while (messages[at]?.role === "toolResult") at++;
	return at;
}

/**
 * The `context` handler that index.ts registers last, outside the gate and the failure guard: it takes every custom
 * message of a feature with a text out of the request, then puts in the whole text of each feature for which `on`
 * holds, in the order of TEXT_ARMS, at the early position. No text goes in a Legion pane. A request with no such
 * message and no text to put in is left as it came.
 */
export function placeTexts(on: (feature: Feature) => boolean): (event: ContextEvent) => { messages: Message[] } | undefined {
	const legion = legionPane();
	const isCopy = (m: Message) => m.role === "custom" && typeof m.customType === "string" && TEXT_TYPES.has(m.customType);
	return event => {
		const placed: Message[] = legion
			? []
			: TEXTS.filter(([feature]) => on(feature)).map(([feature, text]) => ({ role: "custom", customType: feature, content: text, display: false, attribution: "user", timestamp: 0 }));
		const copies = event.messages.some(isCopy);
		if (placed.length === 0 && !copies) return undefined;
		const messages = copies ? event.messages.filter(m => !isCopy(m)) : [...event.messages];
		messages.splice(earlyPosition(messages), 0, ...placed);
		return { messages };
	};
}

/** Marks an element of a payload array that withoutTexts takes out. */
const GONE = Symbol("gone");

/**
 * Whether `value` is a message of a provider payload that holds one of `texts` and nothing else: a user or developer
 * message whose content (or parts) is the text, or one block whose `text` is. That is the shape placeTexts' message
 * takes in each provider's body; a tool result never has it.
 */
function isTextMessage(value: unknown, texts: readonly string[]): boolean {
	if (typeof value !== "object" || value === null || !("role" in value) || (value.role !== "user" && value.role !== "developer")) return false;
	const body = "content" in value ? value.content : "parts" in value ? value.parts : undefined;
	const block: unknown = Array.isArray(body) && body.length === 1 ? body[0] : undefined;
	const only = typeof block === "object" && block !== null && "text" in block ? block.text : body;
	return typeof only === "string" && texts.some(text => text.length === only.length && text === only);
}

/** `value`, a provider payload or a part of it, with every message of isTextMessage taken out. `value` itself when it holds none. */
function withoutTexts(value: unknown, texts: readonly string[]): unknown {
	if (Array.isArray(value)) {
		let out: unknown[] | undefined;
		for (let i = 0; i < value.length; i++) {
			const item = value[i];
			const next = isTextMessage(item, texts) ? GONE : withoutTexts(item, texts);
			if (next !== item) out ??= value.slice(0, i);
			if (out !== undefined && next !== GONE) out.push(next);
		}
		return out ?? value;
	}
	if (typeof value !== "object" || value === null) return value;
	let out: Record<string, unknown> | undefined;
	for (const [key, item] of Object.entries(value)) {
		const next = withoutTexts(item, texts);
		if (next !== item) (out ??= { ...value })[key] = next;
	}
	return out ?? value;
}

/**
 * The `before_provider_request` handler that index.ts registers last, outside the gate and the failure guard: it takes
 * out of the payload the text of each feature for which `on` does not hold, and every text in a Legion pane. So a text
 * that placeTexts put in goes out only while its feature is on and has not stopped, also when the exposure append of
 * this same request threw, and a cache-warming replay of an older request sends no text whose arm is no longer on.
 */
export function scrubPayload(on: (feature: Feature) => boolean): (event: ProviderRequestEvent) => unknown {
	const legion = legionPane();
	return event => {
		const out = TEXTS.filter(([feature]) => legion || !on(feature)).map(([, text]) => text);
		if (out.length === 0) return undefined;
		const payload = withoutTexts(event.payload, out);
		return payload === event.payload ? undefined : payload;
	};
}

/** Whether `entries` hold an exposure entry of `feature` in `arm` under `gate` after their latest `experiments` entry. */
function exposedSinceRecorded(entries: readonly Entry[], feature: Feature, arm: ExposureRecord["arm"], gate: ExposureRecord["gate"]): boolean {
	for (let i = entries.length - 1; i >= 0; i--) {
		const e = entries[i];
		if (e.type !== "custom") continue;
		if (e.customType === ENTRY_TYPE) return false;
		const data = e.data as Partial<ExposureRecord> | undefined;
		if (e.customType === EXPOSURE_ENTRY_TYPE && data?.feature === feature && data.arm === arm && data.gate === gate) return true;
	}
	return false;
}

/**
 * Registers on `pi` the two handlers that record the exposure of `feature`, whose text is `text`. index.ts gives a view
 * of `pi` that runs them only while the gate of `feature` is `on` or `random` under a recorded resolution, and stops
 * `feature` when one throws. `recorded` reads that resolution. Each `context` event owes the entry of the recorded
 * resolution, or in a Legion pane the excluded entry, unless the session file holds it already. The next provider
 * request writes it, in the on arm only when its payload carries `text`. They change no request.
 */
export function recordExposure(pi: ExtensionAPI, feature: Feature, text: string | undefined, recorded: () => ExperimentRecord): void {
	const legion = legionPane();
	// The recorded resolution whose entry this binding wrote or found.
	let done: ExperimentRecord | undefined;
	// The recorded resolution whose entry the latest conversion owes, until a provider request writes it.
	let owed: ExperimentRecord | undefined;
	pi.on("context", ((_event: ContextEvent, ctx: Ctx) => {
		owed = undefined;
		const record = recorded();
		if (record === done) return undefined;
		const { gate, on } = record.features[feature];
		if (gate === "off") return undefined;
		const entries = ctx.sessionManager.getEntries();
		const written = legion
			? entries.some(e => e.type === "custom" && e.customType === EXCLUDED_ENTRY_TYPE && (e.data as Partial<ExcludedRecord> | undefined)?.feature === feature)
			: exposedSinceRecorded(entries, feature, on ? "on" : "off", gate);
		if (written) done = record;
		else owed = record;
		return undefined;
	}) as never);
	pi.on("before_provider_request", ((event: ProviderRequestEvent, ctx: Ctx) => {
		const record = owed;
		if (record === undefined || record !== recorded()) return undefined;
		const { gate, on } = record.features[feature];
		if (gate === "off") return undefined;
		// A request that went past the context handlers carries no text: in the on arm it is not the exposure.
		if (!legion && on && text !== undefined && withoutTexts(event.payload, [text]) === event.payload) return undefined;
		const sm = ctx.sessionManager;
		const rootId = rootSessionId(sm.getSessionFile(), sm.getSessionId());
		const at = new Date().toISOString();
		if (legion) {
			const exclusion: ExcludedRecord = { v: 1, feature, reason: "legion_pane", rootSessionId: rootId, at };
			pi.appendEntry(EXCLUDED_ENTRY_TYPE, exclusion);
		} else {
			const exposure: ExposureRecord = { v: 1, feature, arm: on ? "on" : "off", gate, rootSessionId: rootId, at };
			// A throw here stops the feature (index.ts), and scrubPayload then takes its text out of this payload.
			pi.appendEntry(EXPOSURE_ENTRY_TYPE, exposure);
		}
		owed = undefined;
		done = record;
		return undefined;
	}) as never);
}
