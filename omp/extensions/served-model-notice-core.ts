// Tell a session, top-level or subagent, when a model other than the one it was asked to run is
// serving it, and record when Anthropic made the change.
//
// Three events substitute a model, and before this the model saw none of them (proven on omp
// 18.13.3 with a stub provider, 2026-10-29: the system prompt kept `Model: <asked>` and no message
// changed):
//   * omp's own fallback chain (`retry.fallbackChains`). After repeated 429s, a refusal or a quota
//     wall, omp moves the session down the chain and emits `retry_fallback_applied`. Only the
//     transcript's `model_change` entry and the parent's task-result header record it; neither
//     reaches the model. Lanes that "stop and report on a model failure" could not see the
//     substitution, and a subagent's report of its own model named the one it was asked to run.
//   * omp starting a session on a replacement, when the model asked for has no usable credential
//     or its usage window is spent. No event fires; the session's first `model_change` entry says
//     `resolvedModelIsFallback: true`, and the parent's header names the model asked for.
//   * Anthropic's in-request fallback. Anthropic can serve a declined request from a fallback
//     model inside the same request and then route that conversation to the fallback for about an
//     hour; omp sends the `fallbacks` parameter for the Fable and Mythos families. omp keeps the
//     handoff as a `fallback` content block, but replays that block only to Anthropic's official
//     endpoint, so behind a gateway the model never sees it (dispatch://AGENTC-319).
//
// What it does:
//   * On `retry_fallback_applied`, a persisted custom message naming the asked and the serving
//     model, sent as a steer: omp drains it into the very request the fallback model answers, so
//     even a final turn knows. Only the session's own fallback counts (fallback-event.ts); an
//     advisor's, which omp reports through the same event, does not. The Fireworks Fast degrade,
//     which omp reports the same way, keeps the model and only drops the tier, so it is skipped.
//   * On `session_start`, when the session's first `model_change` says omp started it on a
//     replacement, the same notice. The model asked for is the role the session was spawned for
//     (`session_init.modelRole`, read through `readRole`); a subagent spawned with an explicit
//     model, and a top-level session, carry no record of it, and the notice says so.
//   * On an assistant message carrying a `fallback` block, or served by a model the session was
//     not on at any point since the previous message, the `served-by-other-model` entry the
//     refusal survey reads, and a notice. Not "differs from the current model": refusal-cooldown.ts
//     switches the model from its `context` hook, after which omp still sends that turn's request
//     to the old model (observed on omp 18.13.3: `context` runs before `turn_start`), and that turn
//     is omp's, not Anthropic's. This notice can only follow the substituted turn, so it rides the
//     next request (`aside`) when tool calls follow, and waits for the next prompt (`nextTurn`) when
//     the turn ended the run or called `yield`: an aside there would start an extra turn whose
//     reply would replace the result.
//   * A walk is one substitution: it starts at a fallback (or a startup replacement), continues
//     through steps whose `model_change` entry follows the walk's latest one, and keeps the model
//     first asked for. When the asked model serves again, one `restored` notice, so the model stops
//     reporting the fallback model as its own. When omp moves the walk back to a model it already
//     served from (refusal-cooldown restoring a startup replacement), one `moved` notice naming it.
//     A move to any other model is a person's choice and ends the walk silently; a step back onto
//     the asked model itself (a usage wrap, an effort-only step) sends nothing until it serves.
//   * Each notice says this is a substitution, not the hard failure (a refusal that ends the work,
//     or an error) that a "stop and report on a model failure" rule is about, tells the model to
//     carry on and to name both models in its result, and, when the serving model is below the
//     floor, to start the result with a `MODEL BELOW FLOOR` line. That instruction is the Verified
//     Linux lane lead's, 2026-10-29 03:40Z: every result there is machine-checked, so an
//     Anthropic-to-Anthropic fallback costs capability, never correctness.
//   * The floor is one model, `OMP_SERVED_MODEL_FLOOR` (default anthropic/claude-opus-4-8), placed
//     by the configured fallback chains, not by a model ladder written here: the serving model is
//     at or above the floor when a chain lists it at or before the floor (a chain's key ranks
//     above its entries; the chain the event walked is read first), below when after, and treated
//     as below when no chain lists both.
//   * No repeats: one notice per asked/served pair within a walk. A chain that walks several models
//     gives one notice per step, each naming the new model and replacing the last; a new walk with
//     the same pair (the asked model failing again right after a restore, before it served) is told
//     again. A committed compaction that drops the latest notice from context re-sends it once; a
//     notice still queued (not yet on the branch) is left to arrive, and a shake leaves a notice
//     this short in place, so neither is re-sent.
//
// Texts are in served-model-notice.md. State is per agent id, so a subagent never reads its
// parent's, and is dropped when the session switches, branches or moves in its tree.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { type FallbackCtx, newestModelChangeId, ownFallbackClaims } from "./fallback-event";
import texts from "./served-model-notice.md" with { type: "text" };

const NOTICE_TYPE = "served-model-notice";
const FLOOR = process.env.OMP_SERVED_MODEL_FLOOR || "anthropic/claude-opus-4-8";
const REASON_MAX = 200;
/** What a notice says when the session carries no record of the model it was asked to run. */
const ASKED_UNKNOWN = "the model your parent asked for (omp did not record its name in this session)";
const ASKED_UNKNOWN_MAIN = "the model this session was asked to run (omp did not record its name)";

const SECTIONS = new Map<string, string>();
// split() with a capture group gives [preamble, name, body, name, body, ...].
const parts = texts.split(/^<!-- ([\w-]+) -->\n/m);
for (let i = 1; i + 1 < parts.length; i += 2) SECTIONS.set(parts[i], parts[i + 1].trim());
for (const name of ["chain", "startup", "anthropic", "restored", "moved", "top", "below", "unplaced", "tail", "restored-tail"]) {
	if (!SECTIONS.get(name)) throw new Error(`served-model-notice: served-model-notice.md has no "${name}" section`);
}

type Model = { provider: string; id: string };
type Placement = "top" | "below" | "unplaced";
type Head = "chain" | "startup" | "anthropic" | "restored" | "moved";
type BranchEntry = {
	type: string;
	id: string;
	customType?: string;
	details?: unknown;
	firstKeptEntryId?: string;
	model?: string;
	role?: string;
	resolvedModelIsFallback?: boolean;
	modelRole?: string;
};
type Ctx = FallbackCtx & {
	agent: { id: string; kind: string; name: string };
	models: { current(): Model | undefined; resolve(spec: string): Model | undefined };
	isIdle(): boolean;
	sessionManager: { getBranch(): ReadonlyArray<BranchEntry> };
};
type Message = {
	role: string;
	provider?: string;
	model?: string;
	stopReason?: string;
	stopDetails?: { type?: string } | null;
	content?: Array<{ type: string; name?: string; from?: { model: string }; to?: { model: string } }>;
};

interface Sent {
	id: string;
	content: string;
}
interface AgentState {
	/** The fallback in force (chain or startup): what the session was asked to run and what serves it. */
	chain?: {
		asked: string;
		askedModel?: Model;
		served: string;
		placement: Placement;
		entryId?: string;
		/** The model serving now, and every model this walk has served from. */
		servedModel: Model;
		history: Model[];
	};
	/** `asked -> served` of the latest substitution notice; cleared when the asked model serves again. */
	lastPair?: string;
	/** The latest notice sent, re-sent after a compaction that drops it. */
	latest?: Sent;
	/** Every model the session was on since the previous assistant message (see `see`). */
	seen: Model[];
	runContinues: boolean;
}

const same = (a: Model | undefined, b: Model | undefined) => !!a && !!b && a.provider === b.provider && a.id === b.id;
const fill = (text: string, values: Record<string, string>) => text.replace(/\{\{(\w+)\}\}/g, (_, key: string) => values[key] ?? "");
const selector = (model: Model) => `${model.provider}/${model.id}`;

/** Where `served` sits against the floor, from the configured chains; the walked chain is read first. */
function place(served: Model, chains: Record<string, readonly string[]>, walked: string | undefined, resolve: Ctx["models"]["resolve"]): Placement {
	const resolved = new Map<string, Model | undefined>();
	const lookup = (spec: string) => {
		if (!resolved.has(spec)) resolved.set(spec, resolve(spec));
		return resolved.get(spec);
	};
	const floor = lookup(FLOOR);
	if (!floor) return "unplaced";
	const matches = (spec: string, model: Model) => (spec.endsWith("/*") ? model.provider === spec.slice(0, -2) : same(lookup(spec), model));
	const keys = Object.keys(chains).sort((a, b) => Number(b === walked) - Number(a === walked));
	for (const key of keys) {
		const list = key.includes("/") ? [key, ...chains[key]] : chains[key];
		const floorAt = list.findIndex(spec => matches(spec, floor));
		const servedAt = list.findIndex(spec => matches(spec, served));
		if (floorAt >= 0 && servedAt >= 0) return servedAt <= floorAt ? "top" : "below";
	}
	return "unplaced";
}

function compose(head: Head, placement: Placement | undefined, values: Record<string, string>): string {
	const placed = placement ? [SECTIONS.get(placement)] : [];
	const parts = [SECTIONS.get(head), ...placed, SECTIONS.get(head === "restored" ? "restored-tail" : "tail")];
	const part = head === "restored" ? " (part of the work)" : "";
	return `<system-reminder>\n${fill(parts.join("\n\n"), { ...values, floor: FLOOR, part })}\n</system-reminder>`;
}

/**
 * Binds the notice to `pi`. `readChains` returns the session's effective `retry.fallbackChains`;
 * `readRole` returns the selector a model role is configured to.
 */
export function bindServedModelNotice(
	pi: ExtensionAPI,
	readChains: () => Record<string, readonly string[]> | undefined,
	readRole: (role: string) => string | undefined,
): void {
	const agents = new Map<string, AgentState>();
	const own = ownFallbackClaims();
	const state = (ctx: Ctx) => {
		let entry = agents.get(ctx.agent.id);
		if (!entry) {
			entry = { runContinues: false, seen: [] };
			agents.set(ctx.agent.id, entry);
		}
		return entry;
	};
	const chains = (): Record<string, readonly string[]> => {
		try {
			return readChains() ?? {};
		} catch {
			return {};
		}
	};
	let sequence = 0;
	const send = (s: AgentState, content: string, deliverAs: "steer" | "aside" | "nextTurn", details: Record<string, unknown>) => {
		const id = `${Date.now().toString(36)}-${++sequence}`;
		s.latest = { id, content };
		pi.sendMessage({ customType: NOTICE_TYPE, content, display: true, details: { ...details, id } }, { deliverAs });
	};
	/** After a substituted turn: ride the next request when tool calls follow, else wait for the next prompt. */
	const afterTurn = (message: Message, ctx: Ctx): "aside" | "nextTurn" => {
		const yields = message.content?.some(block => block.type === "toolCall" && block.name === "yield");
		return message.stopReason === "toolUse" && !yields && !ctx.isIdle() ? "aside" : "nextTurn";
	};
	const agentLabel = (ctx: Ctx) => `${ctx.agent.kind}:${ctx.agent.name}`;

	pi.on("retry_fallback_applied", async (event, ctx: Ctx) => {
		// Fireworks Fast -> Standard keeps the model; only the serving tier changes.
		if (event.role === "fireworks-fast") return undefined;
		const fallback = own(event, ctx);
		if (!fallback) return undefined;
		const s = state(ctx);
		const current = ctx.models.current();
		if (!current) return undefined;
		s.seen.push(current);
		// A step continues the walk only when its entry directly follows the walk's latest one; after
		// any other model switch (a person's /model, omp's restore) the event starts a new walk.
		const walk = s.chain && fallback.previousEntryId === s.chain.entryId ? s.chain : undefined;
		const asked = walk?.asked ?? event.from;
		const askedModel = walk ? walk.askedModel : ctx.models.resolve(event.from);
		// Back onto the asked model (a usage wrap to the chain's head) or an effort-only step: no
		// substitution. A walk in force is told `restored` when that model next serves.
		if (same(current, askedModel)) {
			if (walk) walk.entryId = fallback.entryId;
			return undefined;
		}
		const served = event.to;
		const pair = `${asked} -> ${served}`;
		if (pair === s.lastPair && walk) {
			walk.entryId = fallback.entryId;
			return undefined;
		}
		const placement = place(current, chains(), event.role, ctx.models.resolve);
		s.chain = { asked, askedModel, served, placement, entryId: fallback.entryId, servedModel: current, history: [...(walk?.history ?? []), current] };
		s.lastPair = pair;
		const raw = "reason" in event && typeof event.reason === "string" ? event.reason : undefined;
		const reason = raw ? raw.split("\n")[0].slice(0, REASON_MAX) : "omp gave no reason";
		const values = { asked, served, reason, from: event.from };
		send(s, compose("chain", placement, values), "steer", { kind: "chain", asked, served, placement, agent: agentLabel(ctx) });
		return undefined;
	});

	pi.on("session_start", (_event: unknown, ctx: Ctx) => {
		const branch = ctx.sessionManager.getBranch();
		const initial = branch.find(entry => entry.type === "model_change");
		if (initial?.resolvedModelIsFallback !== true || !initial.model) return;
		// Told already: a resumed or revived session keeps its notices on the branch.
		if (branch.some(entry => entry.customType === NOTICE_TYPE)) return;
		const current = ctx.models.current();
		if (!current) return;
		const s = state(ctx);
		// A subagent records its spawn role; a top-level session runs the `default` role unless pinned.
		const role = branch.findLast(entry => entry.type === "session_init")?.modelRole ?? (ctx.agent.kind === "main" ? "default" : undefined);
		let configured: string | undefined;
		try {
			configured = role ? readRole(role) : undefined;
		} catch {
			configured = undefined;
		}
		// The asked model may be one this session cannot resolve: having no login is a reason omp replaces it.
		const askedModel = configured ? ctx.models.resolve(configured) : undefined;
		if (same(askedModel, current)) return;
		const asked = configured ?? (ctx.agent.kind === "main" ? ASKED_UNKNOWN_MAIN : ASKED_UNKNOWN);
		const served = selector(current);
		const placement = place(current, chains(), configured?.replace(/:[a-z]+$/, ""), ctx.models.resolve);
		// omp may follow the replacement's entry with a second one for the spawn role, naming the same
		// model; a later chain step follows that one, so the walk starts from the newest.
		const newest = branch.findLast(entry => entry.type === "model_change");
		const entryId = newest?.model && same(ctx.models.resolve(newest.model), current) ? newest.id : initial.id;
		s.chain = { asked, askedModel, served, placement, entryId, servedModel: current, history: [current] };
		s.lastPair = `${asked} -> ${served}`;
		send(s, compose("startup", placement, { asked, served }), "nextTurn", { kind: "startup", asked, served, placement, agent: agentLabel(ctx) });
	});

	// Every model the session is on between two assistant messages: a served model the session was
	// on at any point since the last message is omp's own choice, not Anthropic's.
	const see = (_event: unknown, ctx: Ctx) => {
		const current = ctx.models.current();
		const s = state(ctx);
		if (current && !s.seen.some(model => same(model, current))) s.seen.push({ provider: current.provider, id: current.id });
	};
	pi.on("context", see);
	pi.on("turn_start", see);

	pi.on("message_end", async (event: { message: Message }, ctx: Ctx) => {
		const message = event.message;
		if (message.role !== "assistant" || !message.provider || !message.model) return undefined;
		const s = state(ctx);
		const seen = s.seen;
		const current = ctx.models.current();
		s.seen = current ? [{ provider: current.provider, id: current.id }] : [];
		// Already visible as an error turn; its model is the one that declined, not one that served.
		if (message.stopDetails?.type === "refusal" || message.stopReason === "error" || message.stopReason === "aborted") return undefined;
		// Resolved, so a dated id Anthropic reports for the same model compares and places as that model.
		const servedModel = ctx.models.resolve(`${message.provider}/${message.model}`) ?? { provider: message.provider, id: message.model };

		if (s.chain?.askedModel && same(servedModel, s.chain.askedModel)) {
			const { asked, served, placement } = s.chain;
			s.chain = undefined;
			s.lastPair = undefined;
			const placed = placement === "top" ? undefined : placement;
			send(s, compose("restored", placed, { asked, served }), afterTurn(message, ctx), { kind: "restored", asked, served });
			return undefined;
		}
		// omp moved a walk back to a model it had already served from (refusal-cooldown restoring a
		// startup replacement), still not the one asked: tell the model which one serves now. A move
		// to any other model is a person's choice and ends the walk.
		const walk = s.chain;
		if (walk?.servedModel && !same(servedModel, walk.servedModel) && seen.some(model => same(model, servedModel))) {
			if (!walk.history.some(model => same(model, servedModel))) {
				s.chain = undefined;
				s.lastPair = undefined;
				return undefined;
			}
			const served = selector(servedModel);
			const placement = place(servedModel, chains(), undefined, ctx.models.resolve);
			s.chain = { ...walk, served, servedModel, placement, entryId: newestModelChangeId(ctx) };
			s.lastPair = `${walk.asked} -> ${served}`;
			send(s, compose("moved", placement, { asked: walk.asked, served }), afterTurn(message, ctx), { kind: "moved", asked: walk.asked, served, placement, agent: agentLabel(ctx) });
			return undefined;
		}

		const handoff = message.content?.find(block => block.type === "fallback");
		if (!handoff && seen.some(model => same(model, servedModel))) return undefined;
		const turnAsked = handoff?.from?.model ? `${message.provider}/${handoff.from.model}` : current && selector(current);
		if (!turnAsked || turnAsked === selector(servedModel)) return undefined;
		// As Anthropic reported it, for the survey that reads these entries.
		const served = `${message.provider}/${message.model}`;
		pi.appendEntry("served-by-other-model", {
			agent: agentLabel(ctx),
			asked: turnAsked,
			served,
			// Present only when Anthropic switched inside this request; absent on a routed turn.
			handoff: handoff ? `${handoff.from?.model} -> ${handoff.to?.model}` : undefined,
			at: new Date().toISOString(),
		});
		// During a fallback the model the session was asked to run is the fallback's, not this turn's.
		const asked = s.chain?.asked ?? turnAsked;
		const pair = `${asked} -> ${served}`;
		if (pair === s.lastPair) return undefined;
		s.lastPair = pair;
		const placement = place(servedModel, chains(), undefined, ctx.models.resolve);
		send(s, compose("anthropic", placement, { asked, served }), afterTurn(message, ctx), { kind: "anthropic", asked, served, placement, agent: agentLabel(ctx) });
		return undefined;
	});

	pi.on("turn_end", (event: { toolResults: unknown[] }, ctx: Ctx) => {
		state(ctx).runContinues = event.toolResults.length > 0;
	});
	pi.on("agent_end", (_event: unknown, ctx: Ctx) => {
		state(ctx).runContinues = false;
	});
	// The models seen this turn stay: a restore from another extension can still be in flight.
	const forget = (_event: unknown, ctx: Ctx) => {
		const seen = agents.get(ctx.agent.id)?.seen ?? [];
		agents.set(ctx.agent.id, { runContinues: false, seen });
	};
	pi.on("session_switch", forget);
	pi.on("session_branch", forget);
	pi.on("session_tree", forget);

	// A committed compaction keeps only the summary and the entries from `firstKeptEntryId` on.
	pi.on("session_compact", (_event: unknown, ctx: Ctx) => {
		const s = agents.get(ctx.agent.id);
		if (!s?.latest) return;
		const latestId = s.latest.id;
		const isLatest = (entry: BranchEntry) =>
			entry.customType === NOTICE_TYPE && !!entry.details && typeof entry.details === "object" && "id" in entry.details && entry.details.id === latestId;
		const branch = ctx.sessionManager.getBranch();
		const at = branch.findIndex(isLatest);
		// Still queued: it reaches context after the compaction on its own.
		if (at < 0) return;
		const compaction = branch.findLast(entry => entry.type === "compaction");
		const keptFrom = compaction ? branch.findIndex(entry => entry.id === compaction.firstKeptEntryId) : 0;
		if (keptFrom >= 0 && at >= keptFrom) return;
		send(s, s.latest.content, !ctx.isIdle() && s.runContinues ? "aside" : "nextTurn", { kind: "resent-after-compaction", of: latestId });
	});
}
