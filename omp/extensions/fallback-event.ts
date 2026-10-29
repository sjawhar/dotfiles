// Tells omp's own fallback of a session's model apart from an advisor's, for the extensions that
// act on `retry_fallback_applied` (refusal-cooldown.ts, served-model-notice-core.ts).
//
// omp emits that event from two places (omp 18.13.3): its turn recovery, when it moves the
// session's own model down `retry.fallbackChains`, and its advisor runtime, when an advisor's own
// model fails over. The payloads look the same, and an advisor's step leaves the session's model
// alone. Before emitting the first kind, omp appends a `model_change` entry naming the new model
// with role `fallback` and `resolvedModelIsFallback: true` (turn-recovery.ts,
// applyRetryFallbackCandidate and the Fireworks Fast degrade); the advisor path appends nothing,
// and omp's own restore appends role `fallback` with `resolvedModelIsFallback: false`. So an event
// is the session's own when the newest `model_change` on the branch is such an entry, names the
// event's target, has not been claimed by an earlier event, and follows a `model_change` naming the
// event's `from` (every model switch appends one; the session's first may predate the branch). The
// last two close two holes: an advisor event that follows a session fallback onto the same model
// finds the entry claimed, and one that lands between omp appending the entry and emitting the
// session's event (an awaited prompt rebuild sits between them) names the wrong `from`.

type Model = { provider: string; id: string };
type BranchEntry = {
	type: string;
	id: string;
	model?: string;
	role?: string;
	resolvedModelIsFallback?: boolean;
	thinkingLevel?: string | null;
	configured?: string | null;
};
export type FallbackCtx = {
	agent: { id: string };
	models: { resolve(spec: string): Model | undefined };
	sessionManager: { getBranch(): ReadonlyArray<BranchEntry> };
};

/** The session's own fallback: its `model_change` entry, and the entry before it on the branch. */
export interface OwnFallback {
	entryId: string;
	/** The `model_change` before this fallback's, when the branch holds one. */
	previousEntryId: string | undefined;
	/** Whether the effort configured before this fallback was `auto` (newest `thinking_level_change`). */
	autoEffort: boolean;
}

/** The newest `model_change` on the branch, the one a later model switch would supersede. */
export function newestModelChangeId(ctx: FallbackCtx): string | undefined {
	return ctx.sessionManager.getBranch().findLast(e => e.type === "model_change")?.id;
}

/**
 * Returns a claim check for one extension instance: `own(event, ctx)` gives the fallback once per
 * session fallback, and undefined for an advisor's fallback, a repeat, or an event with no entry.
 */
export function ownFallbackClaims(): (event: { from: string; to: string }, ctx: FallbackCtx) => OwnFallback | undefined {
	const claimed = new Set<string>();
	const same = (a: Model | undefined, b: Model | undefined) => !!a && !!b && a.provider === b.provider && a.id === b.id;
	return (event, ctx) => {
		const branch = ctx.sessionManager.getBranch();
		const at = branch.findLastIndex(e => e.type === "model_change");
		const entry = branch[at];
		if (!entry?.model || entry.role !== "fallback" || entry.resolvedModelIsFallback !== true) return undefined;
		const key = `${ctx.agent.id}:${entry.id}`;
		if (claimed.has(key)) return undefined;
		if (!same(ctx.models.resolve(event.to), ctx.models.resolve(entry.model))) return undefined;
		const earlier = branch.slice(0, at);
		const before = earlier.findLast(e => e.type === "model_change");
		const beforeModel = before?.model ? ctx.models.resolve(before.model) : undefined;
		const fromModel = ctx.models.resolve(event.from);
		// Lenient when either side does not resolve: a missed fallback costs more than this rare race.
		if (beforeModel && fromModel && !same(beforeModel, fromModel)) return undefined;
		claimed.add(key);
		const effort = earlier.findLast(e => e.type === "thinking_level_change");
		return {
			entryId: entry.id,
			previousEntryId: before?.id,
			// Every session not on `auto` records its effort at creation; none recorded means `auto`.
			autoEffort: !effort || effort.configured === "auto" || effort.thinkingLevel === "auto",
		};
	};
}
